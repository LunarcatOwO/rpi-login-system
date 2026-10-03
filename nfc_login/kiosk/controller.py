"""Kiosk behaviour: what happens on a card scan or a key press.

The controller knows nothing about Tkinter. Each handler returns a ``Screen``
describing what the display should show, which keeps this logic testable
without a display or real hardware.

Keypad (Da Vinci Kit 4x4), with the default teams:

    A B C D     start typing a team member's ID, e.g. B 0 0 7 for B007
                (A Robot, B Impact, C Sustainability, D Strategy).
    0-9         digits. From the idle screen a digit starts a mentor's ID,
                which is only a number (007). After any ID: 1 = sign in/out
                with PIN.
    *           backspace; on an empty entry, back / cancel.
                From the idle screen, * opens the admin menu (asks for the PIN).
    #           Enter, once you've started typing.

The admin menu has three submenus, so everything works from the kiosk with
nobody opening the web page:

    1 People    add a user, enroll / remove cards, team, rename, PIN, deactivate
    2 Hours     add / subtract hours, sign someone in or out, who's here,
                sign everyone out, start a new season
    3 System    info, updates, Wi-Fi, admin PIN, restart / close the app,
                restart / shut down the Pi

Names and Wi-Fi passwords need letters, so they're typed on an on-screen
keyboard (or a USB keyboard); those keys arrive via handle_char. Slow jobs
(Wi-Fi, updates, power) run in the background and push their result through
``publish``, which the window sets.

A card imported from the legacy system belongs to a user with a U ID (no
team yet). On its first scan the person picks their own team, which gives
them their real ID, and then they're signed in. Choosing Mentors needs the
admin PIN. Admins can also change anyone's team from the admin menu.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from nfc_login.hardware.nfc_reader import TagWriteError
from nfc_login.services import ids, timefmt
from nfc_login.services.attendance import (
    IGNORED,
    SIGNED_IN,
    SIGNED_OUT,
    AttendanceError,
    AttendanceService,
    UserStats,
    WrongPinError,
)
from nfc_login.services.seasons import SeasonError
from nfc_login.services.system import SystemActionError, WifiOffError
from nfc_login.services.updates import UpdateError
from nfc_login.services.users import UserError, UserService
from nfc_login.tags.payload import TagTooSmall, build_message

log = logging.getLogger(__name__)

IDLE = "idle"
USER_ID = "user_id"           # typing an ID from the idle screen
USER_MENU = "user_menu"       # a user's stats, offering PIN sign-in
USER_PIN = "user_pin"
ADMIN_PIN = "admin_pin"
BUSY = "busy"                 # a background job (Wi-Fi, update, power) is running

ADMIN_MENU = "admin_menu"
PEOPLE_MENU = "people_menu"
HOURS_MENU = "hours_menu"
HERE_LIST = "here_list"       # admin: who is here, a page at a time
SYSTEM_MENU = "system_menu"
MENUS = (ADMIN_MENU, PEOPLE_MENU, HOURS_MENU, SYSTEM_MENU)

# People
NEW_TEAM = "new_team"         # adding a user: which team
NEW_NAME = "new_name"         # adding a user: their name, on the screen keyboard
ENROLL_ID = "enroll_id"
ENROLL_SCAN = "enroll_scan"
UNCARD_ID = "uncard_id"       # whose cards to remove
MOVE_ID = "move_id"           # whose team to change
PICK_TEAM = "pick_team"       # a team is chosen (legacy card's owner, or admin via MOVE_ID)
RENAME_ID = "rename_id"
RENAME_NAME = "rename_name"
USERPIN_ID = "userpin_id"
USERPIN_NEW = "userpin_new"
USERPIN_AGAIN = "userpin_again"
ACTIVE_ID = "active_id"       # whom to deactivate / reactivate
# Hours
ADJUST_ID = "adjust_id"
ADJUST_AMOUNT = "adjust_amount"
TOGGLE_ID = "toggle_id"       # sign someone in or out without a PIN
SEASON_PIN = "season_pin"     # admin PIN again before starting a new season
# System
UPDATE_CONFIRM = "update_confirm"
WIFI_LIST = "wifi_list"
WIFI_PASSWORD = "wifi_password"
ADMINPIN_NEW = "adminpin_new"
ADMINPIN_AGAIN = "adminpin_again"
CONFIRM = "confirm"           # "#  do it   *  back" for the action in context["action"]

ID_STATES = (USER_ID, ENROLL_ID, ADJUST_ID, MOVE_ID, UNCARD_ID, RENAME_ID, USERPIN_ID,
             ACTIVE_ID, TOGGLE_ID)
PIN_STATES = (USER_PIN, ADMIN_PIN, USERPIN_NEW, USERPIN_AGAIN, ADMINPIN_NEW, ADMINPIN_AGAIN,
              SEASON_PIN)
NAME_STATES = (NEW_NAME, RENAME_NAME)
TEXT_STATES = (*NAME_STATES, WIFI_PASSWORD)

# Where * goes from an empty entry.
BACK = {
    PEOPLE_MENU: ADMIN_MENU, HOURS_MENU: ADMIN_MENU, SYSTEM_MENU: ADMIN_MENU,
    NEW_TEAM: PEOPLE_MENU, ENROLL_ID: PEOPLE_MENU, UNCARD_ID: PEOPLE_MENU,
    MOVE_ID: PEOPLE_MENU, RENAME_ID: PEOPLE_MENU, USERPIN_ID: PEOPLE_MENU,
    ACTIVE_ID: PEOPLE_MENU, NEW_NAME: NEW_TEAM, RENAME_NAME: RENAME_ID,
    USERPIN_NEW: USERPIN_ID, USERPIN_AGAIN: USERPIN_NEW,
    ADJUST_ID: HOURS_MENU, TOGGLE_ID: HOURS_MENU, SEASON_PIN: HOURS_MENU, HERE_LIST: HOURS_MENU,
    ADJUST_AMOUNT: ADJUST_ID,
    UPDATE_CONFIRM: SYSTEM_MENU, WIFI_LIST: SYSTEM_MENU, ADMINPIN_NEW: SYSTEM_MENU,
    WIFI_PASSWORD: WIFI_LIST, ADMINPIN_AGAIN: ADMINPIN_NEW,
}

MAX_PIN = 8
MAX_AMOUNT = 4                # HHMM, up to 99h 59m per adjustment
MAX_NAME = 40
SETUP_REMINDER = "Setup changed in an update: run bash scripts/setup-pi.sh"
MAX_PASSWORD = 63             # longest WPA passphrase
MAX_WIFI_LIST = 8
HERE_PAGE = 8                 # people per page on the admin "Who is here" list
MAX_PIN_FAILURES = 5
LOCKOUT_SECONDS = 60
POWER_DELAY_SECONDS = 2       # time to read the screen before the Pi goes down
WIFI_ON_SECONDS = 5           # after switching the radio on, before scanning again

PEOPLE_MENU_LINES = [
    "1  Add a user",
    "2  Enroll a card",
    "3  Remove someone's cards",
    "4  Change someone's team",
    "5  Rename someone",
    "6  Set someone's PIN",
    "7  Deactivate / reactivate",
    "*  Back",
]
HOURS_MENU_LINES = [
    "1  Add / subtract hours",
    "2  Sign someone in or out",
    "3  Who is here",
    "4  Sign everyone out",
    "5  Start a new season",
    "*  Back",
]


@dataclass
class Screen:
    """What the display shows.

    A line made only of "K  label" parts (K a keypad key, two spaces, then the
    label; parts separated by three or more spaces, e.g. "#  yes      *  back")
    describes keys to press. The windows draw such lines as buttons that press
    that key when touched, so everything works by touch or keypad. Other lines
    are plain text: don't put two spaces after a lone key character in them.
    """

    title: str
    lines: list[str] = field(default_factory=list)
    tone: str = "info"                  # info | success | warning | error | prompt
    entry: str | None = None            # what's been typed (already masked for PINs)
    hold_seconds: float | None = None   # return to idle after this long
    refresh_leaderboard: bool = False   # also refreshes the "here now" list
    sound: str | None = None            # buzzer pattern; by default from the tone
    # on-screen keys: "name" / "text" (letters, for names / passwords)
    keyboard: str | None = None
    close_app: bool = False             # the window should close itself
    restart_app: bool = False           # the app should restart itself (e.g. after an update)

    @property
    def buzz(self) -> str | None:
        """Buzzer pattern to play when this screen appears (see hardware/buzzer.py)."""
        return self.sound or {"success": "success", "warning": "warning",
                              "error": "error"}.get(self.tone)


def parse_amount(digits: str) -> int:
    """Keypad amount as H..HMM -> seconds. '130' = 1h 30m, '45' = 45m, '200' = 2h."""
    if not digits:
        return 0
    minutes = int(digits[-2:])
    hours = int(digits[:-2] or 0)
    if minutes >= 60:
        raise ValueError("Minutes must be 00-59 (type 130 for 1h 30m).")
    return (hours * 60 + minutes) * 60


def _start_thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True, name="kiosk-job").start()


class KioskController:
    def __init__(
        self,
        attendance: AttendanceService,
        users: UserService,
        reader=None,
        site_url: str = "",
        write_tags: bool = True,
        result_seconds: float = 6,
        keypad_timeout: float = 30,
        monotonic: Callable[[], float] = time.monotonic,
        seasons=None,
        system=None,
        updates=None,
        spawn: Callable[[Callable[[], None]], None] = _start_thread,
        sleep: Callable[[float], None] = time.sleep,
        reopen_hint: str = "To start it again, restart the Pi.",
    ):
        self.attendance = attendance
        self.users = users
        self.reader = reader
        self.site_url = site_url
        self.write_tags = write_tags
        self.result_seconds = result_seconds
        self.keypad_timeout = keypad_timeout
        self.monotonic = monotonic
        self.seasons = seasons      # SeasonService, for "Start a new season"
        self.system = system        # SystemActions: info, Wi-Fi, power
        self.updates = updates      # UpdateChecker
        self._spawn = spawn
        self._sleep = sleep
        self.reopen_hint = reopen_hint   # shown when the app is closed from the menu
        # The window sets this so background jobs can show their result.
        self.publish: Callable[[Screen], None] | None = None
        # keypad key -> section letter, normally {"A": "A", "B": "B", ...}
        self.section_keys = {s["key"]: s["letter"] for s in users.sections if s["key"]}
        self.mentors = ids.MENTORS in users.section_names
        # PICK_TEAM choices: digit -> section letter, in config order
        self.team_choices = {str(n): s["letter"]
                             for n, s in enumerate(users.sections[:9], start=1)}

        self._lock = threading.RLock()
        self.state = IDLE
        self.buffer = ""
        self.context: dict = {}
        self._job: object | None = None
        self._last_input = monotonic()
        self._pin_failures = 0
        self._locked_until = 0.0

    # ------------------------------------------------------------ screens

    def idle_screen(self) -> Screen:
        teams = [f"{k}  {self.users.team_name(v)}" for k, v in self.section_keys.items()]
        rows = ["      ".join(teams[i:i + 2]) for i in range(0, len(teams), 2)]
        hint = "No card? Type your ID: team letter, then number."
        if self.mentors:
            hint += " Mentors: numbers only."
        lines = ["Hold your card on the reader to sign in or out.", hint, *rows,
                 "#  Type your ID", "Keypad: # enter, * backspace", "*  Admin menu"]
        if self._installing():
            lines = ["Installing an update: cards still work.", *lines]
        return Screen("Tap your card", lines, tone="info")

    def _installing(self) -> bool:
        return self.updates is not None and getattr(self.updates, "installing", False)

    def _result(self, title, lines, tone, refresh=False) -> Screen:
        return Screen(title, lines, tone, hold_seconds=self.result_seconds,
                      refresh_leaderboard=refresh)

    def _reset(self) -> None:
        self.state = IDLE
        self.buffer = ""
        self.context = {}

    def _go(self, state: str, context: dict | None = None) -> Screen:
        self.state, self.buffer = state, ""
        if context is not None:
            self.context = context
        return self._redraw()

    # ------------------------------------------------------------ cards

    def handle_card(self, uid: str) -> Screen:
        with self._lock:
            self._last_input = self.monotonic()
            if self.state == ENROLL_SCAN:
                return self._enroll(uid)
            # Signing in always works, even mid-menu or while a background job
            # runs (its result is then dropped, unless it must be shown).
            self._reset()
            try:
                user = self.attendance.user_for_tag(uid)
            except AttendanceError:
                user = None  # scan_tag below reports why
            if user and user["section"] == ids.UNSORTED:
                return self._legacy_card(uid, user)
            try:
                result = self.attendance.scan_tag(uid)
            except AttendanceError as exc:
                return self._result("Not signed in", [str(exc), f"Card {uid}"], "error")
            screen = self._scan_screen(result.action, result.stats, result.session_seconds,
                                       result.notes)
            if result.action != IGNORED:
                screen.lines.extend(self._write_card(result.stats))
            return screen

    def _legacy_card(self, uid: str, user: dict) -> Screen:
        """An imported card with no team yet: its owner picks one first."""
        self.state = PICK_TEAM
        self.context = {"uid": uid, "user": user, "legacy": True}
        screen = self._redraw()
        screen.sound = "attention"
        return screen

    def _scan_screen(self, action, stats: UserStats, session_seconds, notes) -> Screen:
        summary = [
            f"ID {stats.code}  ·  {self.users.team_name(stats.section)}",
            f"Season {stats.season_name}: {stats.total_text}",
            f"Leaderboard rank: {_rank_text(stats)}",
        ]
        if action == SIGNED_IN:
            title, tone, sound = f"Welcome, {stats.username}!", "success", "sign_in"
            lines = ["Signed in at " + timefmt.format_timestamp(stats.last_sign_in)]
        elif action == SIGNED_OUT:
            title, tone, sound = f"Goodbye, {stats.username}!", "success", "sign_out"
            lines = ["Signed out. This session: " + timefmt.format_duration(session_seconds)]
        else:
            title, tone, sound = stats.username, "warning", "ignored"
            lines = []
        screen = self._result(title, notes + lines + summary, tone, refresh=action != IGNORED)
        screen.sound = sound
        return screen

    def _write_card(self, stats: UserStats) -> list[str]:
        """Write the latest stats onto the card still on the reader."""
        if not (self.write_tags and self.reader):
            return []
        try:
            capacity = self.reader.ndef_capacity()
            if capacity is None:
                return ["(This card type can't store info; sign-in still counted.)"]
            self.reader.write_ndef(build_message(stats, self.site_url, capacity))
        except (TagWriteError, TagTooSmall) as exc:
            return [f"Card info not updated: {exc}"]
        return []

    def _enroll(self, uid: str) -> Screen:
        user = self.context["user"]
        self._reset()
        try:
            self.users.enroll_tag(uid, user["id"])
        except UserError as exc:
            return self._result("Card not enrolled", [str(exc)], "error")
        stats = self.attendance.user_stats(user["id"])
        lines = [f"Card {uid} now belongs to {user['username']} ({user['code']})."]
        lines += self._write_card(stats)
        return self._result("Card enrolled", lines, "success")

    # ------------------------------------------------------------ keypad

    def handle_key(self, key: str) -> Screen | None:
        with self._lock:
            self._last_input = self.monotonic()
            if self.state == BUSY:
                return None   # wait for the background job
            if self._locked_until > self.monotonic():
                return self._result("Keypad locked", ["Too many wrong PINs. Try again soon."],
                                    "error")
            if key == "*":
                return self._star()
            handler = {
                IDLE: self._idle_key,
                USER_MENU: self._user_menu_key,
                ADMIN_MENU: self._admin_choice,
                PEOPLE_MENU: self._people_choice,
                HOURS_MENU: self._hours_choice,
                SYSTEM_MENU: self._system_choice,
                ADJUST_AMOUNT: self._amount_key,
                PICK_TEAM: self._pick_team_key,
                NEW_TEAM: self._new_team_key,
                CONFIRM: self._confirm_key,
                UPDATE_CONFIRM: self._update_confirm_key,
                WIFI_LIST: self._wifi_list_key,
                HERE_LIST: self._here_list_key,
            }.get(self.state)
            if handler:
                return handler(key)
            if self.state in ID_STATES:
                return self._id_key(key)
            if self.state in PIN_STATES:
                return self._pin_key(key)
            if self.state in TEXT_STATES:
                return self._text_key(key)
            return None  # ENROLL_SCAN waits for a card

    def handle_char(self, char: str) -> Screen | None:
        """A key from the on-screen (or USB) keyboard while typing a name or password.

        Letters and the like are typed; "\\b" deletes one and "\\n" is Done.
        """
        with self._lock:
            if self.state not in TEXT_STATES:
                return None
            self._last_input = self.monotonic()
            if char == "\n":
                return self._text_key("#")
            if char == "\b":
                self.buffer = self.buffer[:-1]
                return self._redraw()
            if len(char) != 1 or not char.isprintable():
                return self._redraw()
            if self.state == WIFI_PASSWORD:
                if len(self.buffer) < MAX_PASSWORD:
                    self.buffer += char
                return self._redraw()
            self.buffer = _add_name_char(self.buffer, char)
            return self._redraw()

    def check_timeout(self) -> Screen | None:
        """Drop half-typed keypad input after a period of inactivity."""
        with self._lock:
            if (self.state not in (IDLE, BUSY)
                    and self.monotonic() - self._last_input > self.keypad_timeout):
                self._reset()
                return self.idle_screen()
            return None

    def _star(self) -> Screen:
        if self.state == IDLE:
            if not self.users.admin_pin_set():
                return self._result("No admin PIN",
                                    ["Set one with: python3 -m nfc_login.admin set-admin-pin"],
                                    "warning")
            self.state = ADMIN_PIN
            return self._redraw()
        if self.buffer and not (self.state in ID_STATES and self.buffer.isalpha()):
            self.buffer = self.buffer[:-1]
            return self._redraw()
        # Empty entry (or only the team letter): go back a step.
        self.buffer = ""
        if self.state in MENUS and self.context.pop("viewing", False):
            return self._redraw()
        if self.state == PICK_TEAM and "uid" not in self.context:
            return self._go(MOVE_ID)
        if self.state == ADMIN_PIN and self.context.get("legacy"):
            return self._go(PICK_TEAM)  # back from the mentor check to the team list
        if self.state == ENROLL_SCAN and self.context.get("new"):
            user = self.context["user"]
            self._reset()
            return self._result(f"{user['username']} added", [
                f"Their ID is {user['code']}.",
                "No card yet: enroll one later with People > 2.",
            ], "success", refresh=True)
        if self.state == ENROLL_SCAN:
            return self._go(PEOPLE_MENU, {})
        if self.state == CONFIRM:
            return self._go(self.context.get("back", ADMIN_MENU), {})
        if self.state == USER_PIN:
            self.state = USER_MENU
            return self._user_menu_screen()
        back = BACK.get(self.state)
        if back:
            # Keep what's needed a step back (e.g. the Wi-Fi list); menus start clean.
            return self._go(back, {} if back in MENUS else None)
        self._reset()
        return self.idle_screen()

    def _idle_key(self, key: str) -> Screen:
        if key in self.section_keys or (key.isdigit() and self.mentors):
            self.state = USER_ID
            return self._id_key(key)
        if key == "#":
            return self._go(USER_ID, {})      # then type the ID on the keypad
        return self.idle_screen()

    # -- typing an ID (team letter + digits, or a mentor's digits)

    def _id_key(self, key: str) -> Screen | None:
        if not self.buffer:
            if key in self.section_keys:
                self.buffer = self.section_keys[key]
            elif key.isdigit() and self.mentors:
                self.buffer = key
            return self._redraw()  # must start with a team letter (or a mentor digit)
        if key.isdigit():
            self.buffer += key
            if len(self.buffer) == ids.code_length(self.buffer):
                return self._submit_id()
            return self._redraw()
        if key == "#" and any(c.isdigit() for c in self.buffer):
            return self._submit_id()
        return self._redraw()

    def _submit_id(self) -> Screen:
        code, self.buffer = self.buffer, ""
        try:
            user = self.users.get_by_code(code)
            # Only "Deactivate / reactivate" works on someone who's switched off.
            if not user["is_active"] and self.state != ACTIVE_ID:
                raise UserError(f"{user['code']} is deactivated.")
        except UserError as exc:
            return self._error_keep_state(str(exc))
        self.context = {"user": user}
        if self.state == USER_ID:
            self.state = USER_MENU
            return self._user_menu_screen()
        if self.state == TOGGLE_ID:
            return self._admin_toggle(user)
        if self.state == UNCARD_ID:
            uids = [t["uid"] for t in self.users.list_tags()
                    if t["user_id"] == user["id"] and t["is_active"]]
            if not uids:
                return self._error_keep_state(f"{user['username']} has no cards.")
            return self._confirm("remove_cards", PEOPLE_MENU, user=user, uids=uids)
        if self.state == ACTIVE_ID:
            return self._confirm("toggle_active", PEOPLE_MENU, user=user)
        if self.state == RENAME_ID:
            self.state = RENAME_NAME
            self.buffer = user["username"][:MAX_NAME]   # edit the current name
            self.context["cut"] = len(user["username"]) > MAX_NAME
            return self._redraw()
        nxt = {ENROLL_ID: ENROLL_SCAN, MOVE_ID: PICK_TEAM, USERPIN_ID: USERPIN_NEW,
               ADJUST_ID: ADJUST_AMOUNT}[self.state]
        return self._go(nxt)

    # -- a user's own menu

    def _user_menu_screen(self) -> Screen:
        user = self.context["user"]
        try:
            stats = self.attendance.user_stats(user["id"])
        except AttendanceError as exc:
            self._reset()
            return self._result("Not found", [str(exc)], "error")
        return Screen(f"{stats.username}  ({stats.code})", [
            f"Team: {self.users.team_name(stats.section)}",
            f"Season {stats.season_name}: {stats.total_text}",
            f"Leaderboard rank: {_rank_text(stats)}",
            f"Currently: {'signed in' if stats.signed_in else 'signed out'}",
            f"Last sign in: {timefmt.format_timestamp(stats.last_sign_in)}",
            f"Last sign out: {timefmt.format_timestamp(stats.last_sign_out)}",
            "",
            f"1  Sign {'out' if stats.signed_in else 'in'} with your PIN      *  Done",
        ], "info")

    def _user_menu_key(self, key: str) -> Screen | None:
        if key == "1":
            if not self.context["user"]["pin_hash"]:
                return self._error_keep_state("No PIN set for you. Use your card.",
                                              self._user_menu_screen())
            self.state = USER_PIN
            return self._redraw()
        return None

    # -- PINs

    def _pin_key(self, key: str) -> Screen:
        if key == "D" and self.state == USERPIN_NEW and self.context["user"]["pin_hash"]:
            return self._confirm("remove_pin", PEOPLE_MENU, user=self.context["user"])
        if key.isdigit():
            if len(self.buffer) < MAX_PIN:
                self.buffer += key
            return self._redraw()
        if key != "#":
            return self._redraw()
        pin, self.buffer = self.buffer, ""
        if self.state == USERPIN_NEW and not pin and self.context["user"]["pin_hash"]:
            return self._confirm("remove_pin", PEOPLE_MENU, user=self.context["user"])
        if not pin:
            return self._redraw()
        if self.state == ADMIN_PIN:
            return self._check_admin(pin)
        if self.state == USER_PIN:
            return self._user_sign(pin)
        if self.state == SEASON_PIN:
            return self._start_season(pin)
        if self.state in (USERPIN_NEW, ADMINPIN_NEW):
            if len(pin) < 4:
                return self._error_keep_state("A PIN needs 4 to 8 digits.")
            self.context["pin"] = pin
            return self._go(USERPIN_AGAIN if self.state == USERPIN_NEW else ADMINPIN_AGAIN)
        # USERPIN_AGAIN / ADMINPIN_AGAIN: must match the first one
        first = self.context.pop("pin", None)
        if pin != first:
            self.state = USERPIN_NEW if self.state == USERPIN_AGAIN else ADMINPIN_NEW
            return self._error_keep_state("The two PINs didn't match. Try again.")
        if self.state == USERPIN_AGAIN:
            return self._set_user_pin(pin)
        self.users.set_admin_pin(pin)
        self._reset()
        return self._result("Admin PIN changed", ["Use the new PIN from now on."], "success")

    def _set_user_pin(self, pin: str | None) -> Screen:
        user = self.context["user"]
        self._reset()
        try:
            self.users.set_pin(user["code"], pin)
        except (UserError, ValueError) as exc:
            return self._result("PIN not changed", [str(exc)], "error")
        if pin is None:
            return self._result("PIN removed", [
                f"{user['username']} ({user['code']}) now signs in with a card only."], "success")
        return self._result("PIN set", [
            f"{user['username']} can sign in on the keypad now:",
            f"type {user['code']}, then 1, then the PIN.",
        ], "success")

    def _check_admin(self, pin: str) -> Screen:
        if self.users.check_admin_pin(pin):
            self._pin_failures = 0
            if self.context.get("legacy"):
                return self._set_team(self.context["section"])
            screen = self._go(ADMIN_MENU, {})
            screen.sound = "admin"
            return screen
        return self._wrong_pin()

    def _user_sign(self, pin: str) -> Screen:
        user = self.context["user"]
        try:
            self.attendance.check_keypad_login(user["id"], pin)
        except WrongPinError:
            return self._wrong_pin()
        except AttendanceError as exc:
            self._reset()
            return self._result("Not signed in", [str(exc)], "error")
        self._pin_failures = 0
        self._reset()
        try:
            result = self.attendance.toggle(user["id"], method="keypad")
        except AttendanceError as exc:
            return self._result("Not signed in", [str(exc)], "error")
        return self._scan_screen(result.action, result.stats, result.session_seconds,
                                 result.notes)

    def _wrong_pin(self) -> Screen:
        self._pin_failures += 1
        self._reset()
        if self._pin_failures >= MAX_PIN_FAILURES:
            self._pin_failures = 0
            self._locked_until = self.monotonic() + LOCKOUT_SECONDS
            return self._result("Keypad locked", ["Too many wrong PINs. Try again soon."], "error")
        return self._result("Wrong PIN", ["Please try again."], "error")

    # -- admin menus

    def _admin_choice(self, key: str) -> Screen | None:
        menu = {"1": PEOPLE_MENU, "2": HOURS_MENU, "3": SYSTEM_MENU}.get(key)
        return self._go(menu, {}) if menu else None

    def _people_choice(self, key: str) -> Screen | None:
        state = {"1": NEW_TEAM, "2": ENROLL_ID, "3": UNCARD_ID, "4": MOVE_ID,
                 "5": RENAME_ID, "6": USERPIN_ID, "7": ACTIVE_ID}.get(key)
        return self._go(state, {}) if state else None

    def _hours_choice(self, key: str) -> Screen | None:
        if key == "1":
            return self._go(ADJUST_ID, {})
        if key == "2":
            return self._go(TOGGLE_ID, {})
        if key == "3":
            return self._go(HERE_LIST, {"page": 0})
        if key == "4":
            return self._confirm("sign_out_all", HOURS_MENU,
                                 count=len(self.attendance.currently_signed_in()))
        if key == "5":
            if self.seasons is None:
                return self._error_keep_state("Seasons can't be changed from this kiosk.")
            return self._go(SEASON_PIN, {"name": self.seasons.next_name()})
        return None

    def _system_choice(self, key: str) -> Screen | None:
        if key in ("2", "3", "5", "6", "7", "8") and self._installing():
            return self._error_keep_state("An update is being installed. Try again after.")
        if key == "1":
            self.context["viewing"] = True
            return Screen("System info", self._info_lines() + ["", "*  Back"], "info")
        if key == "2":
            return self._check_updates()
        if key == "3":
            return self._wifi_scan()
        if key == "4":
            return self._go(ADMINPIN_NEW, {})
        action = {"5": "restart_app", "6": "close_app", "7": "reboot", "8": "poweroff"}.get(key)
        if action:
            if action in ("reboot", "poweroff") and self.system is None:
                return self._error_keep_state("The Pi can't be switched off from here.")
            return self._confirm(action, SYSTEM_MENU)
        return None

    def _info_lines(self) -> list[str]:
        reader = self.reader.firmware_version() if self.reader else "no reader"
        if self.system is not None:
            lines = self.system.info_lines()
        else:
            lines = [f"Host: {socket.gethostname()}", f"IP: {local_ip()}"]
        lines.append(f"NFC: {reader}")
        if self.updates is not None and self.updates.available:
            lines.append(f"Update: {self.updates.new_commits} new change(s), System > 2")
        if self._setup_needed():
            lines.append(SETUP_REMINDER)
        return lines

    def _setup_needed(self) -> bool:
        return self.updates is not None and getattr(self.updates, "setup_needed", False)

    # -- confirmations

    def _confirm(self, action: str, back: str, **context) -> Screen:
        return self._go(CONFIRM, {"action": action, "back": back, **context})

    def _confirm_key(self, key: str) -> Screen | None:
        if key != "#":
            return None
        ctx, action = self.context, self.context["action"]
        if action == "sign_out_all":
            self._reset()
            count = self.attendance.sign_out_everyone()
            return self._result("Everyone signed out", [f"Closed {count} session(s)."],
                                "success", refresh=True)
        if action == "remove_cards":
            self._reset()
            for uid in ctx["uids"]:
                try:
                    self.users.remove_tag(uid)
                except UserError:
                    pass  # already gone
            user = ctx["user"]
            return self._result("Cards removed", [
                f"{user['username']}'s old card(s) no longer work.",
                "Their ID and hours stay. Enroll a new card with People > 2.",
            ], "success")
        if action == "toggle_active":
            return self._toggle_active(ctx["user"])
        if action == "remove_pin":
            self.context = {"user": ctx["user"]}
            return self._set_user_pin(None)
        if action == "close_app":
            self._reset()
            return Screen("Closing the kiosk", [self.reopen_hint], "warning", close_app=True)
        if action == "restart_app":
            self._reset()
            return Screen("Restarting the kiosk", ["Back in a few seconds."], "info",
                          restart_app=True)
        if action in ("reboot", "poweroff"):
            return self._power(action)
        return None

    def _toggle_active(self, user: dict) -> Screen:
        self._reset()
        try:
            if user["is_active"]:
                # Close an open session first, so they don't linger in "here now".
                if self.attendance.user_stats(user["id"]).signed_in:
                    self.attendance.toggle(user["id"], method="admin")
                self.users.set_active(user["code"], False)
                return self._result(f"{user['username']} deactivated", [
                    "Their cards and ID no longer work.",
                    "Turn them back on with People > 7.",
                ], "success", refresh=True)
            self.users.set_active(user["code"], True)
        except (UserError, AttendanceError) as exc:
            return self._result("Not changed", [str(exc)], "error")
        return self._result(f"{user['username']} reactivated",
                            [f"{user['code']} and their cards work again."], "success",
                            refresh=True)

    def _admin_toggle(self, user: dict) -> Screen:
        self._reset()
        try:
            result = self.attendance.toggle(user["id"], method="admin")
        except AttendanceError as exc:
            return self._result("Not changed", [str(exc)], "error")
        return self._scan_screen(result.action, result.stats, result.session_seconds,
                                 result.notes)

    def _start_season(self, pin: str) -> Screen:
        if not self.users.check_admin_pin(pin):
            return self._wrong_pin()
        name = self.context["name"]
        self._reset()
        try:
            summary = self.seasons.start_new(name)
        except SeasonError as exc:
            return self._result("Season not started", [str(exc)], "error")
        lines = [f"Season {summary.new_season} has begun. Everyone starts at 0."]
        if summary.old_season:
            lines.append(f"{summary.old_season} is saved in the archive folder.")
        if summary.signed_out:
            lines.append(f"Signed out {summary.signed_out} person(s).")
        return self._result("New season started", lines, "success", refresh=True)

    # -- people: add a user, rename

    def _new_team_key(self, key: str) -> Screen:
        section = self.team_choices.get(key) or self.section_keys.get(key)
        if not section:
            return self._redraw()
        return self._go(NEW_NAME, {"section": section})

    def _text_key(self, key: str) -> Screen:
        if self.state == WIFI_PASSWORD:
            if key.isdigit():           # the keypad's digits type too
                return self.handle_char(key)
            if key == "#":
                password, self.buffer = self.buffer, ""
                return self._wifi_connect(self.context["ssid"], password)
            return self._redraw()
        if key != "#":
            return self._redraw()
        name = self.buffer.strip()
        if not name:
            return self._error_keep_state("Type their name first.")
        if self.state == RENAME_NAME:
            user = self.context["user"]
            if name == user["username"] or (self.context.get("cut")
                                            and name == user["username"][:MAX_NAME]):
                self._reset()   # nothing changed (and a long name isn't cut short)
                return self._result("Name not changed", [user["username"]], "info")
            try:
                self.users.rename(user["code"], name)
            except UserError as exc:
                return self._error_keep_state(str(exc))
            self._reset()
            return self._result("Renamed", [f"{user['username']} is now {name} ({user['code']})."],
                                "success", refresh=True)
        try:
            user = self.users.add(name, self.context["section"])
        except UserError as exc:
            return self._error_keep_state(str(exc))
        screen = self._go(ENROLL_SCAN, {"user": user, "new": True})
        screen.tone, screen.sound = "success", "success"
        return screen

    def _here_list_key(self, key: str) -> Screen | None:
        if key != "#":
            return None
        self.context["page"] += 1        # wraps round in _redraw
        return self._redraw()

    # -- system: background jobs

    def _background(self, screen: Screen, job: Callable[[], object],
                    always: bool = False) -> Screen:
        """Run a slow job off the keypad thread; its result is published later.

        The job returns a result Screen (then back to idle), None (nothing to
        show), or a function that moves on to the next step's screen. A card
        scan meanwhile cancels the menu and the result is dropped, unless
        ``always`` (an installed update must still restart the app).
        """
        token = object()
        self.state, self.buffer, self._job = BUSY, "", token

        def work():
            try:
                result = job()
            except Exception as exc:
                log.exception("kiosk job failed")
                result = self._result("Something went wrong", [str(exc)], "error")
            with self._lock:
                owned = self._job is token and self.state == BUSY
                if self._job is token:
                    self._job = None
                if owned:
                    # The job's time doesn't count as idle: its next screen gets the
                    # full keypad timeout.
                    self._last_input = self.monotonic()
                if not owned and not always:
                    return
                if callable(result):
                    result = result() if owned else None
                elif owned:
                    self._reset()
            if result is not None and self.publish:
                self.publish(result)

        self._spawn(work)
        return screen

    def _check_updates(self) -> Screen:
        if self.updates is None:
            return self._error_keep_state("Updates aren't set up on this kiosk.")

        def job():
            try:
                count = self.updates.check(wait=True)
            except UpdateError as exc:
                return self._result("Couldn't check for updates", [str(exc)], "warning")
            if count is None:
                return self._result("Couldn't check for updates", [
                    "The Pi isn't online, or this copy wasn't installed with git.",
                    "Connect to Wi-Fi with System > 3.",
                ], "warning")
            if count == 0:
                version = self.system.version() if self.system else ""
                return self._result("Up to date", [f"Version {version}" if version else
                                                   "This is the newest version."], "success")
            changes = self.updates.pending_changes()
            return lambda: self._go(UPDATE_CONFIRM, {"count": count, "changes": changes})

        return self._background(Screen("Checking for updates…", ["One moment."], "info"), job)

    def _update_confirm_key(self, key: str) -> Screen | None:
        if key != "#":
            return None

        def job():
            try:
                result = self.updates.install()
            except UpdateError as exc:
                return self._result("Update not installed", [str(exc)], "error")
            return Screen("Update installed", [
                f"Version {result.old} → {result.new}", *result.notes, "",
                "Restarting the kiosk…",
            ], "success", restart_app=True)

        return self._background(Screen("Installing the update…", [
            "Please don't turn off the Pi.",
            "The kiosk restarts by itself when it's done.",
        ], "warning"), job, always=True)

    def _wifi_scan(self) -> Screen:
        if self.system is None:
            return self._error_keep_state("Wi-Fi can't be set up from this kiosk.")

        def job():
            try:
                try:
                    networks = self.system.wifi_networks()
                except WifiOffError:
                    # They asked for Wi-Fi, so switch the radio on and look again.
                    self.system.wifi_on()
                    self._sleep(WIFI_ON_SECONDS)
                    networks = self.system.wifi_networks()
            except SystemActionError as exc:
                return self._result("Wi-Fi", [str(exc)], "error")
            networks = networks[:MAX_WIFI_LIST]
            return lambda: self._go(WIFI_LIST, {"networks": networks})

        return self._background(Screen("Looking for Wi-Fi…", ["One moment."], "info"), job)

    def _wifi_list_key(self, key: str) -> Screen | None:
        if key == "#":
            return self._wifi_scan()
        networks = self.context["networks"]
        if not key.isdigit() or not 1 <= int(key) <= len(networks):
            return None
        network = networks[int(key) - 1]
        if network.secured:
            return self._go(WIFI_PASSWORD, {"networks": networks, "ssid": network.ssid})
        return self._wifi_connect(network.ssid, None)

    def _wifi_connect(self, ssid: str, password: str | None) -> Screen:
        networks = self.context.get("networks", [])

        def job():
            try:
                ip = self.system.wifi_connect(ssid, password)
            except SystemActionError as exc:
                def back_to_list(message=str(exc)):
                    self._go(WIFI_LIST, {"networks": networks})
                    return self._error_keep_state(message)
                return back_to_list
            if self.updates is not None and getattr(self.updates, "automatic", False):
                self._spawn(self.updates.check)   # now online: look for updates
            return self._result("Wi-Fi connected", [f"Connected to {ssid}."]
                                + ([f"IP: {ip}"] if ip else []), "success")

        return self._background(Screen(f"Connecting to {ssid}…", ["This can take 30 seconds."],
                                       "info"), job)

    def _power(self, action: str) -> Screen:
        def job():
            self._sleep(POWER_DELAY_SECONDS)
            try:
                self.system.power(action)
            except SystemActionError as exc:
                return self._result("Not done", [str(exc)], "error")
            return None   # the Pi is going down

        if action == "poweroff":
            screen = Screen("Shutting down", [
                "Wait until the green light on the Pi stops flashing,",
                "then it's safe to unplug it.",
            ], "warning")
        else:
            screen = Screen("Restarting the Pi", ["The kiosk is back in about a minute."],
                            "warning")
        return self._background(screen, job, always=True)

    def _amount_key(self, key: str) -> Screen:
        if key.isdigit():
            if len(self.buffer) < MAX_AMOUNT:
                self.buffer += key
            return self._redraw()
        if key in ("A", "B"):
            try:
                seconds = parse_amount(self.buffer)
            except ValueError as exc:
                self.buffer = ""
                return self._error_keep_state(str(exc))
            if key == "B":
                seconds = -seconds
            user = self.context["user"]
            try:
                stats = self.attendance.adjust(user["id"], seconds, "kiosk keypad", via="kiosk")
            except AttendanceError as exc:
                self.buffer = ""
                return self._error_keep_state(str(exc))
            self._reset()
            verb = "Added" if seconds > 0 else "Subtracted"
            return self._result(f"{verb} {timefmt.format_duration(abs(seconds))}", [
                f"{stats.username} ({stats.code})",
                f"New season total: {stats.total_text}",
                f"Leaderboard rank: {_rank_text(stats)}",
            ], "success", refresh=True)
        return self._redraw()

    # ------------------------------------------------------------ teams

    def _pick_team_key(self, key: str) -> Screen:
        section = self.team_choices.get(key) or self.section_keys.get(key)
        if not section:
            return self._redraw()
        if self.context.get("legacy") and section == ids.MENTORS:
            # People choose their own team, but only an admin can make a mentor.
            if not self.users.admin_pin_set():
                return self._error_keep_state("Mentors need an admin, and no admin PIN is set.")
            self.state = ADMIN_PIN
            self.context["section"] = section
            return self._redraw()
        return self._set_team(section)

    def _set_team(self, section: str) -> Screen:
        uid, user = self.context.get("uid"), self.context["user"]
        self._reset()
        team = self.users.team_name(section)
        try:
            user = self.users.move(user["id"], section)
            if uid is None:
                return self._result("Team changed", [
                    f"{user['username']} is now in {team}.",
                    f"Their ID is now {user['code']}.",
                ], "success", refresh=True)
            result = self.attendance.scan_tag(uid)
        except (UserError, AttendanceError) as exc:
            return self._result("Team not changed", [str(exc)], "error")
        # The card has usually left the reader by now; it's written on the next scan.
        notes = [f"Now in {team}. Your ID is {user['code']}; use it on the keypad.", ""]
        return self._scan_screen(result.action, result.stats, result.session_seconds,
                                 notes + result.notes)

    # ------------------------------------------------------------ drawing

    def _redraw(self) -> Screen:
        state, ctx = self.state, self.context
        if state == ADMIN_MENU:
            lines = ["1  People: add, cards, teams, PINs",
                     "2  Hours and sign-ins",
                     "3  System: Wi-Fi, updates, power"]
            if self.updates is not None and self.updates.available:
                lines.append("     ⬇ Update available: 3, then 2")
            return Screen("Admin menu", lines + ["", "*  Leave this menu"], "prompt")
        if state == PEOPLE_MENU:
            return Screen("People", list(PEOPLE_MENU_LINES), "prompt")
        if state == HOURS_MENU:
            return Screen("Hours and sign-ins", list(HOURS_MENU_LINES), "prompt")
        if state == SYSTEM_MENU:
            update = "2  Check for updates"
            if self.updates is not None and self.updates.available:
                update = f"2  Install update ({self.updates.new_commits} new)"
            lines = [
                "1  System info", update, "3  Wi-Fi", "4  Change the admin PIN",
                "5  Restart the kiosk app", "6  Close the kiosk app",
                "7  Restart the Pi", "8  Shut down the Pi", "*  Back",
            ]
            if self._setup_needed():
                lines.insert(0, SETUP_REMINDER)
            return Screen("System", lines, "prompt")
        if state == USER_MENU:
            return self._user_menu_screen()
        if state == CONFIRM:
            return self._confirm_screen()
        if state == PICK_TEAM:
            user = ctx["user"]
            legacy = ctx.get("legacy")
            choices = [f"{key}  {self.users.team_name(letter)}"
                       + ("  (needs an admin)" if legacy and letter == ids.MENTORS else "")
                       for key, letter in self.team_choices.items()]
            if legacy:
                return Screen(f"Welcome, {user['username']}!", [
                    "Please choose your team before signing in.",
                    *choices, "*  cancel (you won't be signed in)",
                ], "prompt")
            cancel = ("*  cancel (they won't be signed in)" if "uid" in ctx
                      else "*  back")
            return Screen(f"Team for {user['username']}", [
                f"Currently: {self.users.team_name(user['section'])} ({user['code']})",
                *choices, cancel,
            ], "prompt")
        if state == ADMIN_PIN and ctx.get("legacy"):
            user = ctx["user"]
            return Screen(f"{user['username']} as a mentor", [
                "An admin needs to confirm this.",
                "",
                "Admin: type the PIN, then #",
                "*  back to the teams",
            ], "prompt", entry="•" * len(self.buffer))
        if state == NEW_TEAM:
            choices = [f"{key}  {self.users.team_name(letter)}"
                       for key, letter in self.team_choices.items()]
            return Screen("Add a user: team", [*choices, "*  back"], "prompt")
        if state in NAME_STATES:
            if state == NEW_NAME:
                title = f"New {self.users.team_name(ctx['section'])} member's name"
            else:
                title = f"Rename {ctx['user']['code']}"
            lines = ["Type it on the keyboard below, then Done.",
                     "#  done      *  delete / back"]
            if state == RENAME_NAME and ctx.get("cut"):
                lines.insert(0, f"Their name is longer than {MAX_NAME} letters: it will be "
                                "shortened if you change it.")
            return Screen(title, lines, "prompt", entry=self.buffer + "_", keyboard="name")
        if state == WIFI_PASSWORD:
            shown = "•" * max(len(self.buffer) - 1, 0) + self.buffer[-1:]
            return Screen(f"Password for {ctx['ssid']}", [
                "Type it on the keyboard below, then Done.",
                "#  connect      *  delete / back",
            ], "prompt", entry=shown + "_", keyboard="text")
        if state == WIFI_LIST:
            networks = ctx["networks"]
            # Spaces squeezed: 3 in a row would split the line's button in two.
            lines = [f"{n}  {_cut(' '.join(net.ssid.split()), 22)}  {net.signal}%"
                     + ("  locked" if net.secured else "")
                     + ("  (connected)" if net.in_use else "")
                     for n, net in enumerate(networks, start=1)]
            return Screen("Wi-Fi networks", (lines or ["No networks found."])
                          + ["", "#  scan again      *  back"], "prompt")
        if state == HERE_LIST:
            rows = self.attendance.currently_signed_in()
            pages = max(1, -(-len(rows) // HERE_PAGE))
            page = ctx["page"] % pages
            ctx["page"] = page
            lines = [f"{r['code']}  {_cut(r['username'], 22)}  since {r['sign_in_at']:%H:%M}"
                     for r in rows[page * HERE_PAGE:(page + 1) * HERE_PAGE]]
            footer = ([f"Page {page + 1} of {pages}", "#  next page      *  back"] if pages > 1
                      else ["", "*  Back"])
            return Screen(f"Here now: {len(rows)}", (lines or ["Nobody"]) + footer, "info")
        if state == UPDATE_CONFIRM:
            count = ctx["count"]
            lines = [f"•  {change[:44]}" for change in ctx["changes"][:4]]
            if count > len(lines):
                lines.append(f"   ... and {count - len(lines)} more")
            return Screen(f"Update: {count} new change{'s' if count != 1 else ''}", [
                *lines, "",
                "The kiosk restarts after installing.",
                "#  install now      *  back",
            ], "prompt")
        if state == ENROLL_SCAN and ctx.get("new"):
            user = ctx["user"]
            return Screen(f"Added {user['username']}", [
                f"Their ID is {user['code']}.",
                "Tap their card now to enroll it.",
                "*  skip (no card for now)",
            ], "prompt")
        if state == ENROLL_SCAN:
            user = ctx["user"]
            return Screen("Tap the new card",
                          [f"Enrolling for {user['username']} ({user['code']})", "*  cancel"],
                          "prompt")
        if state in ID_STATES:
            title = {USER_ID: "Your user ID", ENROLL_ID: "Enroll a card: user ID",
                     ADJUST_ID: "Adjust hours: user ID", MOVE_ID: "Change team: user ID",
                     UNCARD_ID: "Remove cards: user ID", RENAME_ID: "Rename: user ID",
                     USERPIN_ID: "Set a PIN: user ID",
                     ACTIVE_ID: "Deactivate / reactivate: user ID",
                     TOGGLE_ID: "Sign in or out: user ID"}[state]
            length = ids.code_length(self.buffer) if self.buffer else ids.DIGITS + 1
            shown = self.buffer + "_" * (length - len(self.buffer))
            hint = "Team letter, then the number."
            if self.mentors:
                hint += " Mentors: numbers only."
            return Screen(title, [hint, "# = enter early,  * = back"], "prompt", entry=shown)
        if state in PIN_STATES:
            return self._pin_screen()
        if state == ADJUST_AMOUNT:
            user = ctx["user"]
            try:
                amount = timefmt.format_duration(parse_amount(self.buffer))
            except ValueError:
                amount = "?"
            return Screen(f"Adjust {user['username']} ({user['code']})", [
                "Type the time as hours then minutes:",
                "130 = 1h 30m,  45 = 45m,  200 = 2h",
                "",
                "A  Add       B  Subtract       *  back",
            ], "prompt", entry=f"{self.buffer or '0'}  =  {amount}")
        return self.idle_screen()

    def _pin_screen(self) -> Screen:
        state, ctx = self.state, self.context
        dots = "•" * len(self.buffer)
        if state == USERPIN_NEW:
            user = ctx["user"]
            lines = ["Type a new PIN (4-8 digits), then #"]
            if user["pin_hash"]:
                lines.append("D  Remove their PIN")
            return Screen(f"PIN for {user['username']} ({user['code']})", lines + ["*  back"],
                          "prompt", entry=dots)
        if state == ADMINPIN_NEW:
            return Screen("New admin PIN", ["Type the new PIN (4-8 digits), then #", "*  back"],
                          "prompt", entry=dots)
        if state in (USERPIN_AGAIN, ADMINPIN_AGAIN):
            return Screen("Type the PIN again", ["Then press #", "*  back"], "prompt",
                          entry=dots)
        if state == SEASON_PIN:
            season = self.attendance.active_season_name()
            return Screen(f"Start season {ctx['name']}?", [
                "Everyone's hours go back to 0.",
                f"Season {season} is saved to the archive,",
                "and everyone signed in is signed out.",
                "",
                "Type the admin PIN again, then #, to start it.",
                "*  back",
            ], "warning", entry=dots)
        title = "Admin PIN" if state == ADMIN_PIN else "Your PIN"
        return Screen(title, ["Then press #", "*  back"], "prompt", entry=dots)

    def _confirm_screen(self) -> Screen:
        ctx = self.context
        action = ctx["action"]
        if action == "sign_out_all":
            return Screen("Sign everyone out?", [
                f"{ctx['count']} signed in right now. Their time so far counts.",
                "", "#  sign them out      *  back"], "warning")
        if action == "remove_cards":
            user, uids = ctx["user"], ctx["uids"]
            shown = [f"   {uid}" for uid in uids[:3]]
            if len(uids) > 3:
                shown.append(f"   ... and {len(uids) - 3} more")
            return Screen(f"Remove {user['username']}'s cards?", [
                f"These stop working ({user['code']} and their hours stay):", *shown,
                "", "#  remove      *  back"], "warning")
        if action == "toggle_active":
            user = ctx["user"]
            if user["is_active"]:
                return Screen(f"Deactivate {user['username']}?", [
                    "Their cards and ID stop working and they leave",
                    "the leaderboard. Their history is kept.",
                    "", "#  deactivate      *  back"], "warning")
            return Screen(f"Reactivate {user['username']}?", [
                f"{user['code']} and their cards work again.",
                "", "#  reactivate      *  back"], "prompt")
        if action == "remove_pin":
            user = ctx["user"]
            return Screen(f"Remove {user['username']}'s PIN?", [
                f"{user['code']} can then sign in with a card only.",
                "", "#  remove      *  back"], "warning")
        title, lines = {
            "restart_app": ("Restart the kiosk app?", ["It's back in a few seconds."]),
            "close_app": ("Close the kiosk app?", [
                "The Pi's desktop will show instead.",
                "Card scans won't be counted until it's open again."]),
            "reboot": ("Restart the Pi?", ["The kiosk is back in about a minute."]),
            "poweroff": ("Shut down the Pi?", [
                "Do this before unplugging it.",
                "To turn it on again, unplug it and plug it back in."]),
        }[action]
        return Screen(title, [*lines, "", "#  yes      *  back"], "warning")

    def _error_keep_state(self, message: str, screen: Screen | None = None) -> Screen:
        screen = screen or self._redraw()
        screen.lines = [message] + screen.lines
        screen.tone = "error"
        return screen


def _add_name_char(buffer: str, char: str) -> str:
    """Type one character of a name: capital at the start of each word, no double spaces."""
    if len(buffer) >= MAX_NAME:
        return buffer
    if not buffer or buffer[-1] in " -":
        char = char.upper()
    if char == " " and (not buffer or buffer.endswith(" ")):
        return buffer
    return buffer + char


def _rank_text(stats: UserStats) -> str:
    return f"#{stats.rank} of {stats.ranked_users}" if stats.rank else "-"


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "unknown"


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
