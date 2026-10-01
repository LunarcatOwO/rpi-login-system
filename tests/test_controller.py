from nfc_login.hardware.simulated import SimulatedReader
from nfc_login.kiosk import controller as kc
from nfc_login.tags import ndef


def make(services, capacity=496):
    attendance, _seasons, users = services
    reader = SimulatedReader(capacity=capacity)
    controller = kc.KioskController(attendance, users, reader=reader,
                                    site_url="https://example.github.io/site/")
    return controller, reader, users


def tap(controller, reader, uid):
    reader.tap(uid)
    assert reader.read_uid() == uid
    return controller.handle_card(uid)


def press(controller, keys):
    screen = None
    for key in keys:
        screen = controller.handle_key(key) or screen
    return screen


def card_text(reader, uid):
    records = ndef.decode_message(ndef.unwrap_tlv(reader.memory[uid]))
    return [ndef.record_text(r) for r in records]


def test_scan_writes_stats_to_card(services, clock):
    controller, reader, users = make(services)
    uid = users.add("taylor")
    users.enroll_tag("04AA", uid)
    screen = tap(controller, reader, "04AA")
    assert screen.title == "Welcome, taylor!" and screen.refresh_leaderboard
    clock.advance(hours=1, minutes=5)
    screen = tap(controller, reader, "04AA")
    assert screen.title == "Goodbye, taylor!"
    link, text = card_text(reader, "04AA")
    assert link == f"https://example.github.io/site/?id={uid}"
    assert "Time: 1h 5m" in text and "Rank: #1 of 1" in text


def test_card_type_without_ndef_still_signs_in(services):
    controller, reader, users = make(services, capacity=None)
    users.enroll_tag("11223344", users.add("taylor"))
    screen = tap(controller, reader, "11223344")
    assert screen.title.startswith("Welcome")
    assert any("can't store info" in line for line in screen.lines)


def test_unknown_card_shows_error(services):
    controller, reader, _users = make(services)
    screen = tap(controller, reader, "FFFF")
    assert screen.tone == "error"


def test_admin_enroll_flow(services):
    controller, reader, users = make(services)
    users.set_admin_pin("2468")
    uid = users.add("taylor")
    assert press(controller, "A").title == "Admin PIN"
    screen = press(controller, "2468#")
    assert screen.title == "Admin menu"
    press(controller, "1")
    screen = press(controller, f"{uid}#")
    assert controller.state == kc.ENROLL_SCAN and "taylor" in screen.lines[0]
    screen = tap(controller, reader, "04BEEF")
    assert screen.title == "Card enrolled"
    assert controller.state == kc.IDLE
    assert card_text(reader, "04BEEF")[0].endswith(f"?id={uid}")
    # The new card now signs in.
    assert tap(controller, reader, "04BEEF").title == "Welcome, taylor!"


def test_wrong_admin_pin_locks_after_five_tries(services):
    controller, _reader, users = make(services)
    users.set_admin_pin("2468")
    for _ in range(4):
        assert press(controller, "A1111#").title == "Wrong PIN"
    assert press(controller, "A1111#").title == "Keypad locked"
    assert press(controller, "A").title == "Keypad locked"


def test_keypad_sign_in_with_pin(services, clock):
    controller, _reader, users = make(services)
    uid = users.add("taylor", pin="1357")
    screen = press(controller, f"B{uid}#1357#")
    assert screen.title == "Welcome, taylor!"
    clock.advance(minutes=45)
    screen = press(controller, f"B{uid}#1357#")
    assert screen.title == "Goodbye, taylor!"
    assert press(controller, f"B{uid}#0000#").title == "Wrong PIN"


def test_keypad_lookup_and_cancel(services):
    controller, _reader, users = make(services)
    uid = users.add("taylor")
    screen = press(controller, f"C{uid}#")
    assert screen.title == "taylor" and "Currently: signed out" in screen.lines
    press(controller, "C12")
    assert controller.buffer == "12"
    press(controller, "*")
    assert controller.buffer == "1"
    press(controller, "D")
    assert controller.state == kc.IDLE


def test_keypad_times_out(services):
    attendance, _seasons, users = services
    now = [0.0]
    controller = kc.KioskController(attendance, users, keypad_timeout=30,
                                    monotonic=lambda: now[0])
    controller.handle_key("C")
    now[0] = 31
    assert controller.check_timeout() is not None
    assert controller.state == kc.IDLE
