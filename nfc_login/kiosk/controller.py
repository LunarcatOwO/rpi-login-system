"""Kiosk behaviour: what happens on a card scan or a key press.

The controller knows nothing about Tkinter. Each handler returns a ``Screen``
describing what the display should show, which keeps this logic testable
without a display or real hardware.

Keypad (Da Vinci Kit 4x4), with the default four sections:

    A B C D     start typing a user ID in that section, e.g. B 0 7 for user
                B07. After the ID: 1 = sign in/out with PIN.
    0-9         digits
    *           backspace; on an empty entry, back / cancel.
                From the idle screen, * opens the admin menu (asks for the PIN).
    #           Enter, once you've started typing.

A card imported from the legacy system belongs to a user with a U ID (no
group yet). Its first scan asks them to press their group's letter, which
gives them their real ID, and then signs them in.
"""

from __future__ import annotations

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
from nfc_login.services.users import UserError, UserService
from nfc_login.tags.payload import TagTooSmall, build_message

IDLE = "idle"
USER_ID = "user_id"           # typing an ID from the idle screen
USER_MENU = "user_menu"       # a user's stats, offering PIN sign-in
USER_PIN = "user_pin"
ADMIN_PIN = "admin_pin"
ADMIN_MENU = "admin_menu"
ENROLL_ID = "enroll_id"
ENROLL_SCAN = "enroll_scan"
ADJUST_ID = "adjust_id"
ADJUST_AMOUNT = "adjust_amount"
PICK_GROUP = "pick_group"     # legacy card's first scan: choose a section

ID_STATES = (USER_ID, ENROLL_ID, ADJUST_ID)
PIN_STATES = (USER_PIN, ADMIN_PIN)

MAX_PIN = 8
MAX_AMOUNT = 4                # HHMM, up to 99h 59m per adjustment
MAX_PIN_FAILURES = 5
LOCKOUT_SECONDS = 60

ADMIN_MENU_LINES = [
    "1  Enroll a card",
    "2  Add / subtract hours",
    "3  Who is here",
    "4  Sign everyone out",
    "5  System info",
    "*  Exit",
]


@dataclass
class Screen:
    title: str
    lines: list[str] = field(default_factory=list)
    tone: str = "info"                  # info | success | warning | error | prompt
    entry: str | None = None            # what's been typed (already masked for PINs)
    hold_seconds: float | None = None   # return to idle after this long
    refresh_leaderboard: bool = False   # also refreshes the "here now" list
    sound: str | None = None            # buzzer pattern; by default from the tone

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
    ):
        self.attendance = attendance
        self.users = users
        self.reader = reader
        self.site_url = site_url
        self.write_tags = write_tags
        self.result_seconds = result_seconds
        self.keypad_timeout = keypad_timeout
        self.monotonic = monotonic
        # keypad key -> section letter, normally {"A": "A", "B": "B", ...}
        self.section_keys = {s["key"]: s["letter"] for s in users.sections}

        self._lock = threading.RLock()
        self.state = IDLE
        self.buffer = ""
        self.context: dict = {}
        self._last_input = monotonic()
        self._pin_failures = 0
        self._locked_until = 0.0

    # ------------------------------------------------------------ screens

    def idle_screen(self) -> Screen:
        letters = " ".join(f"{k}={v}" if k != v else k for k, v in self.section_keys.items())
        return Screen(
            "Tap your card",
            ["Hold your card on the reader to sign in or out.",
             "",
             f"No card? Type your ID on the keypad ({letters}).",
             "* = admin"],
            tone="info",
        )

    def _result(self, title, lines, tone, refresh=False) -> Screen:
        return Screen(title, lines, tone, hold_seconds=self.result_seconds,
                      refresh_leaderboard=refresh)

    def _reset(self) -> None:
        self.state = IDLE
        self.buffer = ""
        self.context = {}

    # ------------------------------------------------------------ cards

    def handle_card(self, uid: str) -> Screen:
        with self._lock:
            self._last_input = self.monotonic()
            if self.state == ENROLL_SCAN:
                return self._enroll(uid)
            self._reset()
            try:
                user = self.attendance.user_for_tag(uid)
            except AttendanceError:
                user = None  # scan_tag below reports why
            if user and user["section"] == ids.UNSORTED:
                self.state = PICK_GROUP
                self.context = {"uid": uid, "user": user}
                screen = self._redraw()
                screen.sound = "attention"
                return screen
            try:
                result = self.attendance.scan_tag(uid)
            except AttendanceError as exc:
                return self._result("Not signed in", [str(exc), f"Card {uid}"], "error")
            screen = self._scan_screen(result.action, result.stats, result.session_seconds,
                                       result.notes)
            if result.action != IGNORED:
                screen.lines.extend(self._write_card(result.stats))
            return screen

    def _scan_screen(self, action, stats: UserStats, session_seconds, notes) -> Screen:
        summary = [
            f"ID {stats.code}  ·  Season {stats.season_name}: {stats.total_text}",
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
            if self._locked_until > self.monotonic():
                return self._result("Keypad locked", ["Too many wrong PINs. Try again soon."],
                                    "error")
            if key == "*":
                return self._star()
            handler = {
                IDLE: self._idle_key,
                USER_MENU: self._user_menu_key,
                ADMIN_MENU: self._admin_choice,
                ADJUST_AMOUNT: self._amount_key,
                PICK_GROUP: self._pick_group_key,
            }.get(self.state)
            if handler:
                return handler(key)
            if self.state in ID_STATES:
                return self._id_key(key)
            if self.state in PIN_STATES:
                return self._pin_key(key)
            return None  # ENROLL_SCAN waits for a card

    def check_timeout(self) -> Screen | None:
        """Drop half-typed keypad input after a period of inactivity."""
        with self._lock:
            if self.state != IDLE and self.monotonic() - self._last_input > self.keypad_timeout:
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
        if self.buffer and not (self.state in ID_STATES and len(self.buffer) == 1):
            self.buffer = self.buffer[:-1]
            return self._redraw()
        # Empty entry (or only the section letter): go back a step.
        self.buffer = ""
        if self.state == ADMIN_MENU and self.context.pop("viewing", False):
            return self._redraw()
        if self.state in (ENROLL_ID, ADJUST_ID, ENROLL_SCAN):
            self.state, self.context = ADMIN_MENU, {}
            return self._redraw()
        if self.state == ADJUST_AMOUNT:
            self.state = ADJUST_ID
            return self._redraw()
        if self.state == USER_PIN:
            self.state = USER_MENU
            return self._user_menu_screen()
        self._reset()
        return self.idle_screen()

    def _idle_key(self, key: str) -> Screen:
        if key in self.section_keys:
            self.state = USER_ID
            self.buffer = self.section_keys[key]
            return self._redraw()
        return self.idle_screen()

    # -- typing an ID (section letter + digits)

    def _id_key(self, key: str) -> Screen | None:
        if not self.buffer:
            if key in self.section_keys:
                self.buffer = self.section_keys[key]
                return self._redraw()
            return self._redraw()  # must start with a section letter
        if key.isdigit():
            self.buffer += key
            if len(self.buffer) == 1 + ids.DIGITS:
                return self._submit_id()
            return self._redraw()
        if key == "#" and len(self.buffer) > 1:
            return self._submit_id()
        return self._redraw()

    def _submit_id(self) -> Screen:
        code, self.buffer = self.buffer, ""
        try:
            user = self.users.get_by_code(code)
            if not user["is_active"]:
                raise UserError(f"{user['code']} is deactivated.")
        except UserError as exc:
            return self._error_keep_state(str(exc))
        self.context = {"user": user}
        if self.state == USER_ID:
            self.state = USER_MENU
            return self._user_menu_screen()
        if self.state == ENROLL_ID:
            self.state = ENROLL_SCAN
            return self._redraw()
        self.state = ADJUST_AMOUNT
        return self._redraw()

    # -- a user's own menu

    def _user_menu_screen(self) -> Screen:
        user = self.context["user"]
        try:
            stats = self.attendance.user_stats(user["id"])
        except AttendanceError as exc:
            self._reset()
            return self._result("Not found", [str(exc)], "error")
        return Screen(f"{stats.username}  ({stats.code})", [
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
        if key.isdigit():
            if len(self.buffer) < MAX_PIN:
                self.buffer += key
            return self._redraw()
        if key == "#" and self.buffer:
            pin, self.buffer = self.buffer, ""
            return self._check_admin(pin) if self.state == ADMIN_PIN else self._user_sign(pin)
        return self._redraw()

    def _check_admin(self, pin: str) -> Screen:
        if self.users.check_admin_pin(pin):
            self._pin_failures = 0
            self.state = ADMIN_MENU
            screen = self._redraw()
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

    # -- admin

    def _admin_choice(self, key: str) -> Screen | None:
        if key == "1":
            self.state = ENROLL_ID
            return self._redraw()
        if key == "2":
            self.state = ADJUST_ID
            return self._redraw()
        if key == "3":
            rows = self.attendance.currently_signed_in()
            lines = [f"{r['code']}  {r['username']}  since {r['sign_in_at']:%H:%M}"
                     for r in rows[:8]]
            if len(rows) > 8:
                lines.append(f"... and {len(rows) - 8} more")
            self.context["viewing"] = True
            return Screen(f"Here now: {len(rows)}", (lines or ["Nobody"]) + ["", "*  Back"],
                          "info")
        if key == "4":
            count = self.attendance.sign_out_everyone()
            self._reset()
            return self._result("Everyone signed out", [f"Closed {count} session(s)."],
                                "success", refresh=True)
        if key == "5":
            reader = self.reader.firmware_version() if self.reader else "no reader"
            self.context["viewing"] = True
            return Screen("System info", [f"Host: {socket.gethostname()}",
                                          f"IP: {local_ip()}", f"NFC: {reader}",
                                          "", "*  Back"], "info")
        return None

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

    # ------------------------------------------------------------ drawing

    def _pick_group_key(self, key: str) -> Screen:
        if key not in self.section_keys:
            return self._redraw()
        uid, user = self.context["uid"], self.context["user"]
        self._reset()
        try:
            user = self.users.move(user["id"], self.section_keys[key])
            result = self.attendance.scan_tag(uid)
        except (UserError, AttendanceError) as exc:
            return self._result("Not signed in", [str(exc)], "error")
        # The card has usually left the reader by now; it's written on the next scan.
        notes = [f"Your ID is now {user['code']}. Use it on the keypad.", ""] + result.notes
        return self._scan_screen(result.action, result.stats, result.session_seconds, notes)

    def _redraw(self) -> Screen:
        if self.state == ADMIN_MENU:
            return Screen("Admin menu", list(ADMIN_MENU_LINES), "prompt")
        if self.state == USER_MENU:
            return self._user_menu_screen()
        if self.state == PICK_GROUP:
            user = self.context["user"]
            choices = [f"{key}  {self.users.section_names[letter]}"
                       for key, letter in self.section_keys.items()]
            return Screen(f"Welcome, {user['username']}!", [
                "Your card is from the old system. Which group are you in?",
                "Press its letter on the keypad:",
                "", *choices, "", "*  cancel (you won't be signed in)",
            ], "prompt")
        if self.state == ENROLL_SCAN:
            user = self.context["user"]
            return Screen("Tap the new card",
                          [f"Enrolling for {user['username']} ({user['code']})", "* to cancel"],
                          "prompt")
        if self.state in ID_STATES:
            title = {USER_ID: "Your user ID", ENROLL_ID: "Enroll a card: user ID",
                     ADJUST_ID: "Adjust hours: user ID"}[self.state]
            shown = self.buffer + "_" * (1 + ids.DIGITS - len(self.buffer))
            return Screen(title, ["Section letter, then the number.", "*  back"],
                          "prompt", entry=shown)
        if self.state in PIN_STATES:
            title = "Admin PIN" if self.state == ADMIN_PIN else "Your PIN"
            return Screen(title, ["Then press #", "*  back"], "prompt",
                          entry="•" * len(self.buffer))
        if self.state == ADJUST_AMOUNT:
            user = self.context["user"]
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

    def _error_keep_state(self, message: str, screen: Screen | None = None) -> Screen:
        screen = screen or self._redraw()
        screen.lines = [message] + screen.lines
        screen.tone = "error"
        return screen


def _rank_text(stats: UserStats) -> str:
    return f"#{stats.rank} of {stats.ranked_users}" if stats.rank else "-"


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "unknown"
