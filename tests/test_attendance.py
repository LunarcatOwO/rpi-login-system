import pytest

from nfc_login.services.attendance import IGNORED, SIGNED_IN, SIGNED_OUT, AttendanceError


def test_sign_in_then_out_credits_time(services, clock):
    attendance, _seasons, users = services
    uid = users.add("taylor", "A")["id"]
    users.enroll_tag("04aabbccddeeff", uid)

    first = attendance.scan_tag("04AABBCCDDEEFF")
    assert first.action == SIGNED_IN
    assert first.stats.signed_in

    clock.advance(hours=2, minutes=15, seconds=30)
    second = attendance.scan_tag("04AABBCCDDEEFF")
    assert second.action == SIGNED_OUT
    assert second.session_seconds == 2 * 3600 + 15 * 60 + 30
    assert second.stats.hours_minutes == (2, 15)
    assert second.stats.last_sign_out == clock.now
    assert not second.stats.signed_in


def test_quick_rescan_is_ignored(services, clock):
    attendance, _seasons, users = services
    uid = users.add("taylor", "A")["id"]
    assert attendance.toggle(uid, "card").action == SIGNED_IN
    clock.advance(seconds=3)
    assert attendance.toggle(uid, "card").action == IGNORED
    clock.advance(minutes=30)
    assert attendance.toggle(uid, "card").action == SIGNED_OUT
    clock.advance(seconds=3)
    assert attendance.toggle(uid, "card").action == IGNORED


def test_forgotten_sign_out_earns_nothing(services, clock):
    attendance, _seasons, users = services
    uid = users.add("taylor", "A")["id"]
    attendance.toggle(uid, "card")
    clock.advance(hours=20)
    result = attendance.toggle(uid, "card")
    assert result.action == SIGNED_IN
    assert result.stats.total_seconds == 0
    assert result.notes


def test_unknown_and_removed_cards_rejected(services):
    attendance, _seasons, users = services
    with pytest.raises(AttendanceError):
        attendance.scan_tag("DEADBEEF")
    uid = users.add("taylor", "A")["id"]
    users.enroll_tag("DEADBEEF", uid)
    users.remove_tag("DEADBEEF")
    with pytest.raises(AttendanceError):
        attendance.scan_tag("DEADBEEF")


def test_rank_updates_every_scan(services, clock):
    attendance, _seasons, users = services
    a, b = users.add("alex", "A")["id"], users.add("taylor", "A")["id"]
    attendance.toggle(a, "card")
    attendance.toggle(b, "card")
    clock.advance(hours=1)
    assert attendance.toggle(a, "card").stats.rank == 1
    clock.advance(hours=1)
    result = attendance.toggle(b, "card")
    assert result.stats.rank == 1 and result.stats.ranked_users == 2
    assert attendance.user_stats(a).rank == 2


def test_card_cannot_be_stolen_by_another_user(services):
    _attendance, _seasons, users = services
    a, b = users.add("alex", "A")["id"], users.add("taylor", "A")["id"]
    users.enroll_tag("CAFE", a)
    from nfc_login.services.users import UserError
    with pytest.raises(UserError):
        users.enroll_tag("CAFE", b)


def test_sign_out_everyone_and_close_stale(services, clock):
    attendance, _seasons, users = services
    a, b = users.add("alex", "A")["id"], users.add("taylor", "A")["id"]
    attendance.toggle(a, "card")
    clock.advance(hours=13)
    attendance.toggle(b, "card")
    assert attendance.close_stale_sessions() == 1
    clock.advance(hours=1)
    assert attendance.sign_out_everyone() == 1
    assert attendance.user_stats(a).total_seconds == 0
    assert attendance.user_stats(b).total_seconds == 3600


def test_ids_are_per_section_and_fill_gaps(services):
    _attendance, _seasons, users = services
    assert users.add("a1", "A")["code"] == "A001"
    assert users.add("a2", "a")["code"] == "A002"
    assert users.add("d1", "D")["code"] == "D001"
    assert users.get_by_code("d1")["username"] == "d1"
    from nfc_login.services.users import UserError
    with pytest.raises(UserError):
        users.add("eve", "E")         # only four sections


def test_admin_adjustments_count_toward_total_and_rank(services, clock):
    attendance, _seasons, users = services
    a, b = users.add("alex", "A")["id"], users.add("taylor", "B")["id"]
    attendance.toggle(a, "card")
    clock.advance(hours=1)
    attendance.toggle(a, "card")
    stats = attendance.adjust(b, 2 * 3600, "missed scans", via="web")
    assert stats.total_seconds == 7200 and stats.rank == 1
    stats = attendance.adjust(b, -30 * 60, "", via="cli")
    assert stats.hours_minutes == (1, 30)
    with pytest.raises(AttendanceError):
        attendance.adjust(b, -2 * 3600)          # can't go below zero
    with pytest.raises(AttendanceError):
        attendance.adjust(b, 0)
    history = attendance.recent_adjustments()
    assert [h["seconds"] for h in history] == [-1800, 7200]
    assert history[1]["reason"] == "missed scans" and history[1]["code"] == "B001"
