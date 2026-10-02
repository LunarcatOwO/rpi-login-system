import csv

import pytest

from nfc_login.services.seasons import SeasonError


def test_season_reset_keeps_users_and_history(services, clock):
    attendance, seasons, users = services
    uid = users.add("taylor", "A")["id"]
    users.enroll_tag("CAFE", uid)
    attendance.scan_tag("CAFE")
    clock.advance(hours=3)
    attendance.scan_tag("CAFE")
    clock.advance(minutes=5)
    attendance.scan_tag("CAFE")          # still signed in at reset time
    clock.advance(minutes=30)

    old_name = seasons.active()["name"]
    summary = seasons.start_new("2027")
    assert summary.old_season == old_name
    assert summary.signed_out == 1

    # Fresh hours in the new season, same user and card.
    stats = attendance.user_stats(uid)
    assert stats.season_name == "2027"
    assert stats.total_seconds == 0
    assert not stats.signed_in
    assert attendance.scan_tag("CAFE").stats.season_name == "2027"

    # Old season still has its 3h 30m, and was archived to CSV.
    season, board = seasons.leaderboard(old_name)
    assert not season["is_active"]
    assert board[0].total_seconds == 3 * 3600 + 30 * 60
    board_csv = next(p for p in summary.archive_files if p.name.endswith("leaderboard.csv"))
    rows = list(csv.DictReader(board_csv.open()))
    assert rows[0]["username"] == "taylor" and rows[0]["hours"] == "3" and rows[0]["minutes"] == "30"
    assert rows[0]["user_id"] == "A001"


def test_adjustments_stay_with_their_season(services):
    attendance, seasons, users = services
    uid = users.add("taylor", "A")["id"]
    attendance.adjust(uid, 3600, "bonus")
    seasons.start_new("2027")
    assert attendance.user_stats(uid).total_seconds == 0
    _season, board = seasons.leaderboard(seasons.list()[0]["name"])
    assert board[0].total_seconds == 3600


def test_duplicate_season_name_rejected(services):
    _attendance, seasons, _users = services
    with pytest.raises(SeasonError):
        seasons.start_new(seasons.active()["name"])
    assert len([s for s in seasons.list() if s["is_active"]]) == 1
