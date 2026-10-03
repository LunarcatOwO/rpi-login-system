"""The kiosk screen served for the Electron app (nfc_login/ui/web_kiosk.py)."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from nfc_login.hardware.simulated import SimulatedReader
from nfc_login.kiosk import controller as kc
from nfc_login.kiosk.nfc_worker import NfcWorker
from nfc_login.ui.web_kiosk import WebKiosk, serve


@pytest.fixture
def kiosk(services):
    attendance, _seasons, users = services
    reader = SimulatedReader()
    controller = kc.KioskController(attendance, users, reader=reader)
    kiosk = WebKiosk(controller, {"leaderboard_size": 10}, simulated_reader=reader)
    server = serve(kiosk, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    NfcWorker(reader, controller, kiosk.publish).start()
    yield kiosk, users, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def post(url, data, headers=None):
    req = urllib.request.Request(url, data=data.encode(), headers=headers or {})
    return json.load(urllib.request.urlopen(req))


def next_screen(q, title, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = q.get(timeout=timeout)
        if event["type"] == "screen" and event["screen"]["title"] == title:
            return event["screen"]
    raise AssertionError(f"no {title!r} screen")


def test_keys_and_taps_reach_the_kiosk(kiosk):
    kiosk, users, base = kiosk
    users.enroll_tag("04AA", users.add("taylor", "B")["id"])
    q = kiosk.listen()
    post(base + "/key", "key=b")
    assert next_screen(q, "Your user ID")["entry"] == "B___"
    post(base + "/key", "key=*")
    post(base + "/tap", "uid=04AA")
    screen = next_screen(q, "Welcome, taylor!")
    assert "ID B001  ·  Impact" in screen["lines"]
    side = json.load(urllib.request.urlopen(base + "/api/kiosk"))
    assert side["here"][0]["team"] == "Impact" and side["simulated"]
    assert [k["key"] for k in side["keys"]] == list("ABCD") and side["mentors"]


def test_other_web_pages_cant_press_keys(kiosk):
    _kiosk, _users, base = kiosk
    for headers in ({"Origin": "http://evil.example"}, {"Host": "evil.example"}):
        with pytest.raises(urllib.error.HTTPError) as err:
            post(base + "/key", "key=1", headers)
        assert err.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as err:
        post(base + "/key", "key=Z")
    assert err.value.code == 400


def test_screen_keyboard_types_a_name(kiosk):
    kiosk, users, base = kiosk
    users.set_admin_pin("2468")
    q = kiosk.listen()
    for key in "*2468#113":     # admin menu, People, Add a user, team 3
        post(base + "/key", f"key={key}")
    assert next_screen(q, "New Sustainability member's name")["keyboard"] == "name"
    for char in ["r", "i", "v", "back", "x"]:
        post(base + "/type", f"char={char}")
    post(base + "/type", "char=done")
    assert next_screen(q, "Added Rix")["keyboard"] is None
    assert users.get_by_code("C001")["username"] == "Rix"
    with pytest.raises(urllib.error.HTTPError) as err:
        post(base + "/type", "char=toolong")
    assert err.value.code == 400


def test_update_icon_flag(kiosk):
    kiosk, _users, base = kiosk
    assert json.load(urllib.request.urlopen(base + "/api/kiosk"))["update"] is False
    kiosk.updates = type("U", (), {"available": True})()
    assert json.load(urllib.request.urlopen(base + "/api/kiosk"))["update"] is True


def test_background_jobs_can_publish(kiosk):
    kiosk, _users, _base = kiosk
    assert kiosk.controller.publish == kiosk.publish


def test_keyboard_mode_reaches_the_page(kiosk):
    kiosk, _users, _base = kiosk
    q = kiosk.listen()
    kiosk.publish(kc.Screen("Wi-Fi password for School", ["Type it, then Done."], "prompt",
                            entry="•••a", keyboard="text"))
    assert next_screen(q, "Wi-Fi password for School")["keyboard"] == "text"
    assert kiosk.current()["keyboard"] == "text"
    kiosk.publish(kc.Screen("Old style", keyboard=True))   # older controllers: names
    assert next_screen(q, "Old style")["keyboard"] == "name"
    kiosk.publish(kc.Screen("No keyboard"))
    assert next_screen(q, "No keyboard")["keyboard"] is None


def test_key_buttons_and_no_onscreen_keypad(kiosk):
    kiosk, users, base = kiosk
    users.set_admin_pin("2468")
    q = kiosk.listen()
    # The idle screen's "*  Admin menu" button posts /key; the PIN goes on the keypad.
    post(base + "/key", "key=*")
    pin = next_screen(q, "Admin PIN")
    assert pin["keyboard"] is None and "*  back" in pin["lines"]
    post(base + "/key", "key=*")
    assert next_screen(q, "Tap your card")["keyboard"] is None
    post(base + "/key", "key=#")      # "#  Type your ID" on the idle screen
    assert next_screen(q, "Your user ID")["keyboard"] is None
    page = urllib.request.urlopen(base + "/").read().decode()
    assert 'id="keypad"' not in page and "function keyParts(" in page


def test_password_symbols_survive_the_trip(kiosk, monkeypatch):
    kiosk, _users, base = kiosk
    typed = []
    monkeypatch.setattr(kiosk.controller, "handle_char", typed.append)
    chars = list("&=+#%/\\'\"<> Q;?") + ["back", "done"]
    for char in chars:
        # Encoded like the page's URLSearchParams (a space becomes "+").
        post(base + "/type", urllib.parse.urlencode({"char": char}))
    deadline = time.monotonic() + 3
    while len(typed) < len(chars) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert typed == chars[:-2] + ["\b", "\n"]
