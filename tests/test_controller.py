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
    user = users.add("taylor", "C")
    users.enroll_tag("04AA", user["id"])
    screen = tap(controller, reader, "04AA")
    assert screen.title == "Welcome, taylor!" and screen.refresh_leaderboard
    clock.advance(hours=1, minutes=5)
    screen = tap(controller, reader, "04AA")
    assert screen.title == "Goodbye, taylor!"
    link, text = card_text(reader, "04AA")
    assert link == "https://example.github.io/site/?id=C01"
    assert "ID: C01" in text and "Time: 1h 5m" in text and "Rank: #1 of 1" in text


def test_card_type_without_ndef_still_signs_in(services):
    controller, reader, users = make(services, capacity=None)
    users.enroll_tag("11223344", users.add("taylor", "A")["id"])
    screen = tap(controller, reader, "11223344")
    assert screen.title.startswith("Welcome")
    assert any("can't store info" in line for line in screen.lines)


def test_unknown_card_shows_error(services):
    controller, reader, _users = make(services)
    assert tap(controller, reader, "FFFF").tone == "error"


def test_enrolling_needs_admin_pin(services):
    controller, reader, users = make(services)
    users.set_admin_pin("2468")
    user = users.add("taylor", "B")
    # Without the PIN, letters only start an ID lookup; there's no way to enroll.
    press(controller, "B01")
    assert controller.state == kc.USER_MENU
    press(controller, "*")
    assert press(controller, "*").title == "Admin PIN"
    assert press(controller, "2468#").title == "Admin menu"
    press(controller, "1")
    screen = press(controller, "B01")                    # auto-submits after two digits
    assert controller.state == kc.ENROLL_SCAN and "taylor" in screen.lines[0]
    screen = tap(controller, reader, "04BEEF")
    assert screen.title == "Card enrolled" and controller.state == kc.IDLE
    assert card_text(reader, "04BEEF")[0].endswith("?id=B01")
    assert tap(controller, reader, "04BEEF").title == "Welcome, taylor!"
    assert user["code"] == "B01"


def test_section_d_and_enter_key(services):
    controller, _reader, users = make(services)
    users.add("dee", "D")
    assert press(controller, "D01").title == "dee  (D01)"
    # # is Enter mid-ID, so "D1#" is D01 too.
    press(controller, "*")
    assert press(controller, "D1#").title == "dee  (D01)"


def test_wrong_admin_pin_locks_after_five_tries(services):
    controller, _reader, users = make(services)
    users.set_admin_pin("2468")
    for _ in range(4):
        assert press(controller, "*1111#").title == "Wrong PIN"
    assert press(controller, "*1111#").title == "Keypad locked"
    assert press(controller, "*").title == "Keypad locked"


def test_keypad_sign_in_with_pin(services, clock):
    controller, _reader, users = make(services)
    users.add("taylor", "A", pin="1357")
    press(controller, "A01")
    assert controller.state == kc.USER_MENU
    assert press(controller, "11357#").title == "Welcome, taylor!"
    clock.advance(minutes=45)
    assert press(controller, "A0111357#").title == "Goodbye, taylor!"
    assert press(controller, "A0110000#").title == "Wrong PIN"


def test_user_without_pin_is_told_to_use_card(services):
    controller, _reader, users = make(services)
    users.add("taylor", "A")
    screen = press(controller, "A011")
    assert screen.tone == "error" and "Use your card" in screen.lines[0]


def test_unknown_id_keeps_typing(services):
    controller, _reader, _users = make(services)
    screen = press(controller, "D42")
    assert screen.tone == "error" and "D42" in screen.lines[0]
    assert controller.state == kc.USER_ID


def test_admin_adjust_hours_on_keypad(services):
    controller, _reader, users = make(services)
    users.set_admin_pin("2468")
    users.add("taylor", "A")
    press(controller, "*2468#2A01")
    assert controller.state == kc.ADJUST_AMOUNT
    screen = press(controller, "130")
    assert screen.entry == "130  =  1h 30m"
    screen = press(controller, "A")
    assert screen.title == "Added 1h 30m" and "New season total: 1h 30m" in screen.lines
    screen = press(controller, "*2468#2A01")
    screen = press(controller, "200B")
    assert screen.tone == "error" and "only has 1h 30m" in screen.lines[0]
    screen = press(controller, "45B")
    assert screen.title == "Subtracted 0h 45m"


def test_admin_who_is_here(services):
    controller, reader, users = make(services)
    users.set_admin_pin("2468")
    users.enroll_tag("CAFE", users.add("taylor", "D")["id"])
    tap(controller, reader, "CAFE")
    screen = press(controller, "*2468#3")
    assert screen.title == "Here now: 1" and screen.lines[0].startswith("D01  taylor")
    assert press(controller, "*").title == "Admin menu"
    press(controller, "*")
    assert controller.state == kc.IDLE


def test_backspace_and_timeout(services):
    attendance, _seasons, users = services
    now = [0.0]
    controller = kc.KioskController(attendance, users, keypad_timeout=30,
                                    monotonic=lambda: now[0])
    press(controller, "C1")
    assert controller.buffer == "C1"
    press(controller, "*")
    assert controller.buffer == "C"
    press(controller, "*")
    assert controller.state == kc.IDLE
    press(controller, "B")
    now[0] = 31
    assert controller.check_timeout() is not None
    assert controller.state == kc.IDLE


def test_buzzer_patterns_for_kiosk_events(services, clock):
    controller, reader, users = make(services)
    users.enroll_tag("04AA", users.add("taylor", "C")["id"])
    users.set_admin_pin("2468")
    assert tap(controller, reader, "04AA").buzz == "sign_in"
    assert tap(controller, reader, "04AA").buzz == "ignored"        # too soon
    clock.advance(minutes=20)
    assert tap(controller, reader, "04AA").buzz == "sign_out"
    assert tap(controller, reader, "0BAD").buzz == "error"          # unknown card
    controller.handle_key("*")
    assert press(controller, "2468#").buzz == "admin"
