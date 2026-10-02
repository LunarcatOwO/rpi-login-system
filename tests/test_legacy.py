"""Import from the legacy aesom-e/attendance database, using its own schema."""

from datetime import datetime

import pymysql
import pytest

from nfc_login.legacy.importer import LegacyImporter, read_legacy
from nfc_login.legacy.rfid import legacy_key_for_uid, uid_for_legacy_key

# Schema copied from the legacy readme.
LEGACY_SCHEMA = [
    """CREATE TABLE users (`userId` INT UNSIGNED NOT NULL AUTO_INCREMENT,
        `name` VARCHAR(32) NOT NULL, `hours` DECIMAL(10,2) UNSIGNED NOT NULL,
        `rfidKey` BIGINT UNSIGNED UNIQUE NOT NULL, `loggedIn` BOOLEAN NOT NULL,
        `lastLogin` DATETIME NOT NULL DEFAULT '0000-00-00 00:00:00',
        `lastLogout` DATETIME NOT NULL DEFAULT '0000-00-00 00:00:00',
        PRIMARY KEY (`userId`)) ENGINE = InnoDB""",
    """CREATE TABLE pastseasons (`userId` INT UNSIGNED NOT NULL,
        `hours` DECIMAL(10,2) UNSIGNED NOT NULL, `name` VARCHAR(32) NOT NULL,
        `seasonStartDate` DATE NOT NULL) ENGINE = InnoDB""",
    """CREATE TABLE records (`recordId` INT UNSIGNED NOT NULL AUTO_INCREMENT,
        `userId` INT UNSIGNED NOT NULL, `startTime` DATETIME NOT NULL,
        `endTime` DATETIME NOT NULL, `notes` VARCHAR(64),
        PRIMARY KEY (`recordId`)) ENGINE = InnoDB""",
]

ANA_KEY = 0xDEADBEEF22          # 4-byte card DEADBEEF, as SimpleMFRC522 reports it
BEN_KEY = 0x8804A1B29F          # 7-byte card 04A1B2C3D4E5F6: only partly stored


def test_legacy_key_conversion():
    assert legacy_key_for_uid("DEADBEEF") == ANA_KEY
    assert uid_for_legacy_key(ANA_KEY) == "DEADBEEF"
    assert legacy_key_for_uid("04A1B2C3D4E5F6") == BEN_KEY
    assert legacy_key_for_uid("8804A1B2") == BEN_KEY       # what an RC522 reads
    assert uid_for_legacy_key(BEN_KEY) is None
    assert uid_for_legacy_key(123456) is None              # bad check byte


@pytest.fixture
def legacy(db):
    settings = dict(db.settings, database="nfc_legacy_test")
    conn = pymysql.connect(host=settings["host"], port=settings["port"], user=settings["user"],
                           password=settings["password"])
    with conn.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS nfc_legacy_test")
        cur.execute("CREATE DATABASE nfc_legacy_test")
        cur.execute("USE nfc_legacy_test")
        for sql in LEGACY_SCHEMA:
            cur.execute(sql)
        cur.executemany(
            "INSERT INTO users (name, hours, rfidKey, loggedIn, lastLogin, lastLogout) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [("Ana", "12.50", ANA_KEY, 0, "2026-09-30 15:00:00", "2026-09-30 18:00:00"),
             ("Ben", "3.25", BEN_KEY, 1, "2026-10-01 14:00:00", "2026-09-29 17:00:00"),
             ("Test User", "0", 0, 0, "0000-00-00 00:00:00", "0000-00-00 00:00:00")])
        cur.executemany(
            "INSERT INTO pastseasons (userId, hours, name, seasonStartDate) "
            "VALUES (%s, %s, %s, %s)",
            [(1, "40.00", "Ana", "2024-01-08"), (2, "10.50", "Ben", "2024-01-08"),
             (1, "55.00", "Ana", "2025-01-06")])
        cur.executemany(
            "INSERT INTO records (userId, startTime, endTime) VALUES (%s, %s, %s)",
            [(1, "2026-09-30 15:00:00", "2026-09-30 18:00:00"),
             (2, "2026-09-29 14:00:00", "2026-09-29 17:00:00")])
    conn.commit()
    conn.close()
    yield read_legacy(settings)


def test_import_users_cards_hours_and_seasons(services, db, legacy, clock):
    attendance, seasons, users = services
    summary = LegacyImporter(db, ["B", "C"], clock=clock).run(legacy)
    assert summary.users == 3 and summary.cards == 2 and summary.cards_need_scan == 1
    assert summary.signed_in == 1 and summary.records == 2
    assert summary.seasons == ["Legacy 2024-01-08", "Legacy 2025-01-06"]
    assert [c for _id, c, _n in summary.created] == ["B001", "B002", "B003"]

    ana = users.get_by_code("B001")
    stats = attendance.user_stats(ana["id"])
    assert stats.hours_minutes == (12, 30) and stats.rank == 1
    assert stats.last_sign_in == datetime(2026, 9, 30, 15) and not stats.signed_in
    assert stats.last_sign_out == datetime(2026, 9, 30, 18)

    ben = users.get_by_code("B02")
    assert attendance.user_stats(ben["id"]).signed_in       # still signed in

    # Ana's card scans straight away; Ben's 7-byte card matches by its legacy
    # number on a PN532, and the stored UID is updated to the full one.
    assert attendance.user_for_tag("DEADBEEF")["id"] == ana["id"]
    assert attendance.user_for_tag("04A1B2C3D4E5F6")["id"] == ben["id"]
    assert [t["uid"] for t in users.list_tags() if t["user_id"] == ben["id"]] == \
        ["04A1B2C3D4E5F6"]
    # ... and an RC522 reading the same card still finds him.
    assert attendance.user_for_tag("8804A1B2")["id"] == ben["id"]

    _season, board = seasons.leaderboard("Legacy 2024-01-08")
    assert [(e.username, e.total_seconds) for e in board if e.total_seconds] == \
        [("Ana", 40 * 3600), ("Ben", 10 * 3600 + 1800)]
    _season, board = seasons.leaderboard("Legacy 2025-01-06")
    assert board[0].username == "Ana" and board[0].total_seconds == 55 * 3600
    assert seasons.active()["name"] == "2026"

    # Signing Ben out credits real time on top of the imported hours.
    clock.now = datetime(2026, 10, 1, 16, 0)
    result = attendance.scan_tag("04A1B2C3D4E5F6")
    assert result.action == "signed_out"
    assert result.stats.total_seconds == int(3.25 * 3600) + 2 * 3600


def test_import_is_safe_to_rerun_and_dry_run_saves_nothing(services, db, legacy, clock):
    _attendance, _seasons, users = services
    dry = LegacyImporter(db, ["A"], clock=clock).run(legacy, dry_run=True)
    assert dry.users == 3 and users.list() == []
    LegacyImporter(db, ["A"], clock=clock).run(legacy)
    again = LegacyImporter(db, ["A"], clock=clock).run(legacy)
    assert again.users == 0 and again.skipped_users == 3 and again.records == 0
    assert len(users.list()) == 3


def test_name_clash_and_card_clash(services, db, legacy, clock):
    _attendance, _seasons, users = services
    existing = users.add("Ana", "A")
    users.enroll_tag("DEADBEEF", existing["id"])
    summary = LegacyImporter(db, ["A"], clock=clock).run(legacy)
    names = [n for _id, _c, n in summary.created]
    assert "Ana (old 1)" in names
    assert any("already belongs" in w for w in summary.warnings)


def test_people_pick_their_own_team_on_an_old_cards_first_scan(services, db, legacy, clock):
    from nfc_login.hardware.simulated import SimulatedReader
    from nfc_login.kiosk import controller as kc

    attendance, _seasons, users = services
    users.set_admin_pin("2468")
    clock.now = datetime(2026, 10, 1, 18, 0)                 # after the legacy data
    summary = LegacyImporter(db, clock=clock).run(legacy)
    assert [code for _id, code, _n in summary.created] == ["U001", "U002", "U003"]
    controller = kc.KioskController(attendance, users, reader=SimulatedReader())

    screen = controller.handle_card("DEADBEEF")              # Ana, signed out
    assert controller.state == kc.PICK_TEAM and screen.title == "Welcome, Ana!"
    assert screen.lines[0] == "Please choose your team before signing in."
    assert "3  Sustainability" in screen.lines
    assert "5  Mentors  (needs an admin)" in screen.lines
    assert screen.buzz == "attention"
    assert controller.handle_key("9").title == "Welcome, Ana!"   # not a choice: ask again
    screen = controller.handle_key("3")
    assert screen.title == "Welcome, Ana!" and "Your ID is C001" in screen.lines[0]
    assert controller.state == kc.IDLE
    assert users.get_by_code("C001")["username"] == "Ana"

    clock.advance(minutes=30)
    assert controller.handle_card("DEADBEEF").title == "Goodbye, Ana!"   # no question now

    controller.handle_card("04A1B2C3D4E5F6")                 # Ben, 7-byte card, signed in
    assert controller.handle_key("*").title == "Tap your card"           # cancel
    assert users.get_by_code("U002")["username"] == "Ben"

    # Mentors need the admin PIN; * goes back to the team list.
    controller.handle_card("04A1B2C3D4E5F6")
    screen = controller.handle_key("5")
    assert controller.state == kc.ADMIN_PIN and screen.title == "Ben as a mentor"
    assert controller.handle_key("*").title == "Welcome, Ben!"
    assert controller.state == kc.PICK_TEAM
    controller.handle_key("5")
    for key in "1111#":                                      # not the admin: no team, no sign-in
        screen = controller.handle_key(key)
    assert screen.title == "Wrong PIN" and controller.state == kc.IDLE
    assert users.get_by_code("U002")["username"] == "Ben"
    controller.handle_card("04A1B2C3D4E5F6")
    controller.handle_key("5")
    for key in "2468#":
        screen = controller.handle_key(key)
    assert screen.title == "Goodbye, Ben!"
    assert users.get_by_code("001")["username"] == "Ben"


def test_mentor_choice_on_an_old_card_without_an_admin_pin(services, db, legacy, clock):
    from nfc_login.hardware.simulated import SimulatedReader
    from nfc_login.kiosk import controller as kc

    attendance, _seasons, users = services
    clock.now = datetime(2026, 10, 1, 18, 0)
    LegacyImporter(db, clock=clock).run(legacy)
    controller = kc.KioskController(attendance, users, reader=SimulatedReader())
    controller.handle_card("DEADBEEF")
    screen = controller.handle_key("5")
    assert controller.state == kc.PICK_TEAM and "no admin PIN" in screen.lines[0]
    assert controller.handle_key("1").title == "Welcome, Ana!"   # a team still works
    assert users.get_by_code("A001")["username"] == "Ana"


def test_u_is_reserved(tmp_path):
    from nfc_login.config import load_config
    path = tmp_path / "config.toml"
    path.write_text('[[sections]]\nletter = "U"\nname = "x"\nkey = "A"\n')
    with pytest.raises(ValueError, match="reserved"):
        load_config(path)
