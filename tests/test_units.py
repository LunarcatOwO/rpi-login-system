from nfc_login.config import load_config
from nfc_login.services import timefmt
from nfc_login.services.leaderboard import rank_totals
from nfc_login.services.pins import hash_pin, verify_pin


def test_duration_formatting():
    assert timefmt.split_hours_minutes(3 * 3600 + 7 * 60 + 59) == (3, 7)
    assert timefmt.format_duration(59) == "0h 00m"
    assert timefmt.format_duration(36_000) == "10h 00m"


def test_competition_ranking_with_ties():
    rows = [
        {"user_id": 1, "username": "bo", "total_seconds": 100},
        {"user_id": 2, "username": "Al", "total_seconds": 300},
        {"user_id": 3, "username": "cy", "total_seconds": 100},
        {"user_id": 4, "username": "di", "total_seconds": 0},
    ]
    ranked = [(e.rank, e.username) for e in rank_totals(rows)]
    assert ranked == [(1, "Al"), (2, "bo"), (2, "cy"), (4, "di")]


def test_pin_hash_round_trip():
    stored = hash_pin("1234")
    assert verify_pin("1234", stored)
    assert not verify_pin("4321", stored)
    assert not verify_pin("1234", None)


def test_config_defaults_and_override(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[database]\npassword = "pw"\n[hardware]\nmode = "simulated"\n')
    config = load_config(path)
    assert config.database["password"] == "pw"
    assert config.database["host"] == "localhost"
    assert config.hardware["mode"] == "simulated"
    assert config.hardware["keypad"]["rows"] == [18, 23, 24, 25]
