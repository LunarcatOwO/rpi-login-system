import pytest

from nfc_login.config import load_config
from nfc_login.kiosk.controller import parse_amount
from nfc_login.services.ids import format_code, parse_code
from nfc_login.services import timefmt
from nfc_login.services.leaderboard import rank_totals
from nfc_login.services.pins import hash_pin, verify_pin


def test_duration_formatting():
    assert timefmt.split_hours_minutes(3 * 3600 + 7 * 60 + 59) == (3, 7)
    assert timefmt.format_duration(59) == "0h 00m"
    assert timefmt.format_duration(36_000) == "10h 00m"


def test_competition_ranking_with_ties():
    rows = [
        {"user_id": 1, "code": "A01", "section": "A", "username": "bo", "total_seconds": 100},
        {"user_id": 2, "code": "A02", "section": "A", "username": "Al", "total_seconds": 300},
        {"user_id": 3, "code": "B01", "section": "B", "username": "cy", "total_seconds": 100},
        {"user_id": 4, "code": "D01", "section": "D", "username": "di", "total_seconds": 0},
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


def test_user_codes():
    assert format_code("A", 7) == "A007"
    assert format_code("M", 7) == "007"
    assert parse_code(" b12 ") == ("B", 12)
    assert parse_code("A999") == ("A", 999)
    assert parse_code("012") == ("M", 12)
    for bad in ["AA", "A", "A1000", "A000", "000", "0012", ""]:
        with pytest.raises(ValueError):
            parse_code(bad)


def test_keypad_amounts():
    assert parse_amount("130") == 90 * 60
    assert parse_amount("45") == 45 * 60
    assert parse_amount("200") == 2 * 3600
    with pytest.raises(ValueError):
        parse_amount("175")


def test_four_sections_one_per_letter_key():
    config = load_config("/nonexistent.toml")
    assert [(s["letter"], s["key"]) for s in config.sections] == [
        ("A", "A"), ("B", "B"), ("C", "C"), ("D", "D"), ("M", "")]
    assert [s["name"] for s in config.sections] == [
        "Robot", "Impact", "Sustainability", "Strategy", "Mentors"]


def test_sections_must_not_share_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[[sections]]\nletter = "A"\nname = "x"\nkey = "A"\n'
                    '[[sections]]\nletter = "B"\nname = "y"\nkey = "A"\n')
    with pytest.raises(ValueError):
        load_config(path)


def test_example_config_loads():
    config = load_config("config.example.toml")
    assert config.hardware["nfc"]["write_tags"] is True
