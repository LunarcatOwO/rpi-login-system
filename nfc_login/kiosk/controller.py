"""Kiosk behaviour: what happens on a card scan or a key press.

The controller knows nothing about Tkinter. Each handler returns a ``Screen``
describing what the display should show, which keeps this logic testable
without a display or real hardware.

Keypad map (Da Vinci Kit 4x4 keypad):
    A  admin menu (asks for the admin PIN)
    B  sign in/out without a card: user ID, #, PIN, #
    C  check your time and rank: user ID, #
    D  cancel
    *  backspace (or back, when nothing is typed)
    #  enter
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from nfc_login.hardware.nfc_reader import TagWriteError
from nfc_login.services import timefmt
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
ADMIN_PIN = "admin_pin"
ADMIN_MENU = "admin_menu"
ENROLL_ID = "enroll_id"
ENROLL_SCAN = "enroll_scan"
MANUAL_ID = "manual_id"
MANUAL_PIN = "manual_pin"
LOOKUP_ID = "lookup_id"

MAX_ENTRY = 8
MAX_PIN_FAILURES = 5
LOCKOUT_SECONDS = 60

ADMIN_MENU_LINES = [
    "1  Enroll a card for a user",
    "2  Sign everyone out",
    "3  Who is signed in",
    "4  System info",
    "D  Exit",
]


@dataclass
class Screen:
    title: str
    lines: list[str] = field(default_factory=list)
    tone: str = "info"                  # info | success | warning | error | prompt
    entry: str | None = None            # what's been typed (already masked for PINs)
    hold_seconds: float | None = None   # return to idle after this long
    refresh_leaderboard: bool = False


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

        self._lock = threading.RLock()
        self.state = IDLE
        self.buffer = ""
        self.context: dict = {}
        self._last_input = monotonic()
        self._pin_failures = 0
        self._locked_until = 0.0

    # ------------------------------------------------------------ screens

    def idle_screen(self) -> Screen:
        return Screen(
            "Tap your card",
            ["Hold your card on the reader to sign in or out."],
            tone="info",
        )

    def _result(self, title, lines, tone, refresh=False) -> Screen:
        return Screen(title, lines, tone, hold_seconds=self.result_seconds,
                      refresh_leaderboard=refresh)

    def _prompt(self, title, lines=None, masked=False) -> Screen:
        entry = "•" * len(self.buffer) if masked else self.buffer
        return Screen(title, lines or [], tone="prompt", entry=entry)

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
                result = self.attendance.scan_tag(uid)
            except AttendanceError as exc:
                return self._result("Not signed in", [str(exc), f"Card {uid}"], "error")
            screen = self._scan_screen(result.action, result.stats, result.session_seconds,
                                       result.notes)
            if result.action != IGNORED:
                screen.lines.extend(self._write_card(result.stats))
            return screen

    def _scan_screen(self, action, stats: UserStats, session_seconds, notes) -> Screen:
        rank = f"#{stats.rank} of {stats.ranked_users}" if stats.rank else "-"
        summary = [
            f"Season {stats.season_name} total: {stats.total_text}",
            f"Leaderboard rank: {rank}",
        ]
        if action == SIGNED_IN:
            title, tone = f"Welcome, {stats.username}!", "success"
            lines = ["Signed in at " + timefmt.format_timestamp(stats.last_sign_in)]
        elif action == SIGNED_OUT:
            title, tone = f"Goodbye, {stats.username}!", "success"
            lines = ["Signed out. This session: " + timefmt.format_duration(session_seconds)]
        else:
            title, tone = stats.username, "warning"
            lines = []
        return self._result(title, notes + lines + summary, tone,
                            refresh=action != IGNORED)

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
        user_id = self.context["user_id"]
        self._reset()
        try:
            user = self.users.enroll_tag(uid, user_id)
        except UserError as exc:
            return self._result("Card not enrolled", [str(exc)], "error")
        stats = self.attendance.user_stats(user_id)
        lines = [f"Card {uid} now belongs to {user['username']} (ID {user_id})."]
        lines += self._write_card(stats)
        return self._result("Card enrolled", lines, "success")

    # ------------------------------------------------------------ keypad

    def handle_key(self, key: str) -> Screen | None:
        with self._lock:
            self._last_input = self.monotonic()
            if self._locked_until > self.monotonic():
                return self._result("Keypad locked", ["Too many wrong PINs. Try again soon."],
                                    "error")
            if key == "D":
                self._reset()
                return self.idle_screen()
            if key == "*" and self.state != IDLE:
                if self.buffer:
                    self.buffer = self.buffer[:-1]
                    return self._redraw()
                return self._back()
            if key.isdigit() and self.state not in (IDLE, ADMIN_MENU, ENROLL_SCAN):
                if len(self.buffer) < MAX_ENTRY:
                    self.buffer += key
                return self._redraw()
            if key == "#" and self.state not in (IDLE, ADMIN_MENU, ENROLL_SCAN):
                return self._submit()
            if self.state == IDLE:
                return self._idle_key(key)
            if self.state == ADMIN_MENU:
                return self._admin_choice(key)
            return None

    def check_timeout(self) -> Screen | None:
        """Drop half-typed keypad input after a period of inactivity."""
        with self._lock:
            if self.state != IDLE and self.monotonic() - self._last_input > self.keypad_timeout:
                self._reset()
                return self.idle_screen()
            return None

    def _idle_key(self, key: str) -> Screen:
        if key == "A":
            if not self.users.admin_pin_set():
                return self._result("No admin PIN",
                                    ["Set one with: python3 -m nfc_login.admin set-admin-pin"],
                                    "warning")
            self.state = ADMIN_PIN
        elif key == "B":
            self.state = MANUAL_ID
        elif key == "C":
            self.state = LOOKUP_ID
        else:
            return self._result("Keypad", ["A  admin", "B  sign in/out with ID + PIN",
                                           "C  check my time", "D  cancel"], "info")
        return self._redraw()

    def _redraw(self) -> Screen:
        prompts = {
            ADMIN_PIN: ("Admin PIN", ["Enter PIN, then #"], True),
            ENROLL_ID: ("Enroll card", ["Enter the user ID, then #"], False),
            MANUAL_ID: ("Sign in/out without card", ["Enter your user ID, then #"], False),
            MANUAL_PIN: ("Enter your PIN", ["Then press #"], True),
            LOOKUP_ID: ("Check my time", ["Enter your user ID, then #"], False),
        }
        if self.state == ADMIN_MENU:
            return Screen("Admin menu", list(ADMIN_MENU_LINES), "prompt")
        if self.state == ENROLL_SCAN:
            return Screen("Tap the new card",
                          [f"Enrolling for {self.context['username']} (ID {self.context['user_id']})",
                           "D to cancel"], "prompt")
        title, lines, masked = prompts[self.state]
        return self._prompt(title, lines, masked)

    def _back(self) -> Screen:
        if self.state in (ENROLL_ID, ENROLL_SCAN):
            self.state, self.buffer = ADMIN_MENU, ""
            return self._redraw()
        if self.state == MANUAL_PIN:
            self.state, self.buffer = MANUAL_ID, ""
            return self._redraw()
        self._reset()
        return self.idle_screen()

    def _submit(self) -> Screen:
        entry, self.buffer = self.buffer, ""
        if not entry:
            return self._redraw()

        if self.state == ADMIN_PIN:
            if self.users.check_admin_pin(entry):
                self._pin_failures = 0
                self.state = ADMIN_MENU
                return self._redraw()
            return self._wrong_pin()

        if self.state == ENROLL_ID:
            try:
                user = self.users.get(int(entry))
            except UserError as exc:
                return self._error_keep_state(str(exc))
            self.context = {"user_id": user["id"], "username": user["username"]}
            self.state = ENROLL_SCAN
            return self._redraw()

        if self.state == MANUAL_ID:
            self.context = {"user_id": int(entry)}
            self.state = MANUAL_PIN
            return self._redraw()

        if self.state == MANUAL_PIN:
            user_id = self.context["user_id"]
            try:
                self.attendance.check_keypad_login(user_id, entry)
            except WrongPinError:
                return self._wrong_pin()
            except AttendanceError as exc:
                self._reset()
                return self._result("Not signed in", [str(exc)], "error")
            self._pin_failures = 0
            self._reset()
            try:
                result = self.attendance.toggle(user_id, method="keypad")
            except AttendanceError as exc:
                return self._result("Not signed in", [str(exc)], "error")
            return self._scan_screen(result.action, result.stats, result.session_seconds,
                                     result.notes)

        if self.state == LOOKUP_ID:
            self._reset()
            try:
                stats = self.attendance.user_stats(int(entry))
            except AttendanceError as exc:
                return self._result("Not found", [str(exc)], "error")
            rank = f"#{stats.rank} of {stats.ranked_users}" if stats.rank else "-"
            return self._result(stats.username, [
                f"Season {stats.season_name} total: {stats.total_text}",
                f"Leaderboard rank: {rank}",
                f"Currently: {'signed in' if stats.signed_in else 'signed out'}",
                f"Last sign in: {timefmt.format_timestamp(stats.last_sign_in)}",
                f"Last sign out: {timefmt.format_timestamp(stats.last_sign_out)}",
            ], "info")

        return self._redraw()

    def _wrong_pin(self) -> Screen:
        self._pin_failures += 1
        self._reset()
        if self._pin_failures >= MAX_PIN_FAILURES:
            self._pin_failures = 0
            self._locked_until = self.monotonic() + LOCKOUT_SECONDS
            return self._result("Keypad locked", ["Too many wrong PINs. Try again soon."], "error")
        return self._result("Wrong PIN", ["Please try again."], "error")

    def _error_keep_state(self, message: str) -> Screen:
        screen = self._redraw()
        screen.lines = [message] + screen.lines
        screen.tone = "error"
        return screen

    def _admin_choice(self, key: str) -> Screen | None:
        if key == "1":
            self.state = ENROLL_ID
            return self._redraw()
        if key == "2":
            count = self.attendance.sign_out_everyone()
            self._reset()
            return self._result("Everyone signed out", [f"Closed {count} session(s)."],
                                "success", refresh=True)
        if key == "3":
            rows = self.attendance.currently_signed_in()
            lines = [f"{r['username']} since {r['sign_in_at']:%H:%M}" for r in rows[:8]]
            if len(rows) > 8:
                lines.append(f"... and {len(rows) - 8} more")
            return Screen(f"Signed in now: {len(rows)}", lines or ["Nobody"], "info",
                          hold_seconds=None)
        if key == "4":
            reader = self.reader.firmware_version() if self.reader else "no reader"
            return Screen("System info", [f"Host: {socket.gethostname()}",
                                          f"IP: {_local_ip()}", f"NFC: {reader}"], "info")
        return None


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "unknown"
