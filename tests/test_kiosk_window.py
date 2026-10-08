# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Touch screen versions of the keypad (nfc_login/ui/screen_keys.py, kiosk.html,
kiosk_window.py): "K  label" lines become buttons, and the 4x4 keypad on screen."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from nfc_login.kiosk.controller import Screen
from nfc_login.ui.screen_keys import KEYPAD, blocks, key_parts, menu_columns

PAGE = Path(__file__).parents[1] / "nfc_login" / "ui" / "kiosk.html"

KEY_LINES = {
    "1  People": [("1", "People")],
    "*  Admin menu": [("*", "Admin menu")],
    "#  next page      *  back": [("#", "next page"), ("*", "back")],
    "A  Robot      B  Impact": [("A", "Robot"), ("B", "Impact")],
    "#  done      *  delete / back": [("#", "done"), ("*", "delete / back")],
    "A  Add       B  Subtract       *  back": [("A", "Add"), ("B", "Subtract"), ("*", "back")],
    "1  Sign in with your PIN      *  Done": [("1", "Sign in with your PIN"), ("*", "Done")],
    # Two spaces inside a label keep it one part.
    "2  Lab Wifi  80%  locked": [("2", "Lab Wifi  80%  locked")],
    "4  Mentors  (needs an admin)": [("4", "Mentors  (needs an admin)")],
    "  D  Strategy   ": [("D", "Strategy")],
}
TEXT_LINES = [
    "", "   ", "Hold your card on the reader to sign in or out.",
    "Keypad: # enter, * backspace", "# = enter early,  * = back",
    "     ⬇ Update available: 3, then 2", "A007  Rowan Vale  since 15:00",
    "•  Fix the clock", "   ... and 3 more", "1 People", "1  ", "E  Extra",
    "a  lower case", "10  ten", "#  next page      Page 2 of 3", "#  yes   *",
]


@pytest.mark.parametrize("line", KEY_LINES)
def test_key_lines(line):
    assert key_parts(line) == KEY_LINES[line]


@pytest.mark.parametrize("line", TEXT_LINES)
def test_text_lines(line):
    assert key_parts(line) is None


def test_blocks_group_menus_and_rows():
    lines = ["Hold your card.", "", "A  Robot      B  Impact", "#  Mentors", "*  Admin menu",
             "Done?", "1  One"]
    assert blocks(lines) == [
        ("text", "Hold your card."), ("text", ""),
        ("row", [("A", "Robot"), ("B", "Impact")]),
        ("menu", [("#", "Mentors"), ("*", "Admin menu")]),
        ("text", "Done?"), ("menu", [("1", "One")]),
    ]


def test_menus_of_five_or_more_take_two_columns_filled_downwards():
    four, nine = list("1234"), list("12345678*")
    assert menu_columns(four) == [four]
    assert menu_columns(nine) == [list("12345"), list("678*")]
    assert menu_columns(list("1234567*")) == [list("1234"), list("567*")]


def test_keypad_layout():
    assert KEYPAD == ["123A", "456B", "789C", "*0#D"]


def test_web_page_parses_lines_the_same_way():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    html = PAGE.read_text()
    code = "\n".join(re.search(rf"^function {name}\(.*?^}}$", html, re.M | re.S)[0]
                     for name in ("keyParts", "blocks", "menuColumns"))
    lines = [*KEY_LINES, *TEXT_LINES, "1  a", "2  b", "3  c", "4  d", "5  e"]
    script = code + f"""
const lines = {json.dumps(lines)};
console.log(JSON.stringify({{
  parts: lines.map(keyParts), blocks: blocks(lines),
  columns: [4, 5, 9].map(n => menuColumns([...Array(n).keys()])),
}}));"""
    out = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True,
                                    check=True, timeout=30).stdout)

    def plain(value):
        return json.loads(json.dumps(value))

    assert out["parts"] == plain([key_parts(line) for line in lines])
    assert out["blocks"] == plain(blocks(lines))
    assert out["columns"] == plain([menu_columns(list(range(n))) for n in (4, 5, 9)])


def test_web_keyboard_has_a_backtick():
    assert '[..."_\\\\|~<>{}[]^`"]' in PAGE.read_text()


# ---------------------------------------------------------------- the Tk window


class FakeUsers:
    sections = [{"key": "A", "letter": "R", "name": "Robot"},
                {"key": "B", "letter": "I", "name": "Impact"}]

    def team_name(self, section, short=False):
        return section


class FakeAttendance:
    def currently_signed_in(self):
        return []

    def active_season_name(self):
        return "2026"

    def leaderboard(self, size):
        return []


class FakeController:
    mentors = True
    state = "idle"
    publish = None

    def __init__(self):
        self.users, self.attendance = FakeUsers(), FakeAttendance()
        self.keys: list[str] = []
        self.pressed = threading.Event()

    def idle_screen(self):
        return Screen("Tap your card", ["Hold your card on the reader.", "*  Admin menu"])

    def check_timeout(self):
        return None

    def handle_key(self, key):
        self.keys.append(key)
        self.pressed.set()

    def handle_char(self, char):
        return None


@pytest.fixture
def window():
    tkinter = pytest.importorskip("tkinter")
    from nfc_login.ui.kiosk_window import KioskWindow
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display for Tk")
    controller = FakeController()
    win = KioskWindow(root, controller,
                      {"fullscreen": False, "width": 800, "height": 480, "leaderboard_size": 10})
    root.update()
    yield win
    root.destroy()


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def label(win, parent, text):
    """The Label showing `text` under `parent`."""
    found = [w for w in descendants(parent)
             if w.winfo_class() == "Label" and w.cget("text") == text]
    assert found, f"no {text!r} on screen"
    return found[0]


def tap(win, widget, expect):
    win.controller.pressed.clear()
    win.root.update()
    x, y = widget.winfo_rootx() + 3, widget.winfo_rooty() + 3
    for event in ("<ButtonPress-1>", "<ButtonRelease-1>"):
        widget.event_generate(event, x=3, y=3, rootx=x, rooty=y)
    win.root.update()
    assert win.controller.pressed.wait(2)
    deadline = time.monotonic() + 2
    while win.controller.keys[-1:] != [expect] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert win.controller.keys[-1] == expect


def test_tk_key_lines_are_buttons(window):
    window.show(Screen("People", ["1  Add a user", "2  Enroll a card", "3  Remove cards",
                                  "4  Change team", "5  Rename", "*  Back"]))
    window.root.update()
    back = label(window, window.lines_frame, "Back")
    tap(window, back, "*")
    tap(window, label(window, window.lines_frame, "1"), "1")
    # Two columns, filled downwards: Rename and Back on the right.
    assert back.winfo_rootx() > label(window, window.lines_frame, "Add a user").winfo_rootx()
    assert back.winfo_height() >= 20 and back.master.winfo_height() >= 46

    window.show(Screen("Here now: 12", ["R001  Rowan Vale  since 15:00", "Page 1 of 2",
                                        "#  next page      *  back"]))
    window.root.update()
    assert label(window, window.lines_frame, "R001  Rowan Vale  since 15:00")
    nxt = label(window, window.lines_frame, "next page")
    assert nxt.winfo_rooty() == label(window, window.lines_frame, "back").winfo_rooty()
    tap(window, nxt, "#")


def test_tk_keypad_takes_the_side_panels_place(window):
    window.show(Screen("Admin PIN", ["Then press #", "*  back"], "prompt", entry="••",
                       keyboard="keypad"))
    window.root.update()
    assert window.keypad.winfo_ismapped() and not window.side.winfo_ismapped()
    assert label(window, window.keypad, "Enter") and label(window, window.keypad,
                                                           "Delete / Back")
    tap(window, label(window, window.keypad, "#"), "#")
    tap(window, label(window, window.keypad, "7"), "7")
    tap(window, label(window, window.keypad, "D"), "D")
    assert window.keypad.winfo_children()[0].winfo_height() >= 44
    window.show(Screen("Tap your card"))
    window.root.update()
    assert window.side.winfo_ismapped() and not window.keypad.winfo_ismapped()


def test_tk_letter_keyboards_still_work(window):
    from nfc_login.ui.kiosk_window import TEXT_KEYS
    assert "`" in TEXT_KEYS["symbols"][3]
    window.show(Screen("Rename R001", ["#  done      *  delete / back"], keyboard="name"))
    window.root.update()
    assert window.keyboard.winfo_ismapped() and not window.side.winfo_ismapped()
    assert not window.keypad.winfo_ismapped()


def test_tk_footer_always_shows(window):
    window.show(Screen("Lots", [f"Line {n}" for n in range(40)]))
    window.root.update()
    footer = window.footer
    assert footer.winfo_ismapped() and footer.winfo_height() >= 30
    assert footer.winfo_y() + footer.winfo_height() <= window.root.winfo_height()


def test_tk_update_label_comes_and_goes(window):
    window.updates = type("U", (), {"available": True})()
    window._periodic_here()
    assert window.update_label.winfo_manager()
    window.updates.available = False
    window._periodic_here()
    assert not window.update_label.winfo_manager()
