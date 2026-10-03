"""The kiosk windows (kiosk_window.py, kiosk.html): screen lines, keyboards, footer."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from nfc_login.kiosk.controller import Screen

PAGE = Path(__file__).parents[1] / "nfc_login" / "ui" / "kiosk.html"


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


def test_tk_menu_lines_are_text_not_buttons(window):
    # Menus are chosen on the keypad; the lines are only shown.
    window.show(Screen("People", ["1  Add a user", "2  Enroll a card", "*  Back"]))
    window.root.update()
    shown = [w.cget("text") for w in descendants(window.lines_frame)
             if w.winfo_class() == "Label"]
    assert shown == ["1  Add a user", "2  Enroll a card", "*  Back"]
    assert not any(w.bind("<ButtonRelease-1>") for w in descendants(window.lines_frame))


def test_tk_letter_keyboards_still_work(window):
    from nfc_login.ui.kiosk_window import TEXT_KEYS
    assert "`" in TEXT_KEYS["symbols"][3]
    window.show(Screen("Rename R001", ["#  done      *  delete / back"], keyboard="name"))
    window.root.update()
    assert window.keyboard.winfo_ismapped() and not window.side.winfo_ismapped()


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
