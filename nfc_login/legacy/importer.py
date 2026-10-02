"""Import users, cards and hours from the legacy attendance database.

Legacy schema (github.com/aesom-e/attendance, database `attendance`):

    users        userId, name, hours DECIMAL(10,2), rfidKey, loggedIn,
                 lastLogin, lastLogout
    pastseasons  userId, hours, name, seasonStartDate   (one row per user per old season)
    records      recordId, userId, startTime, endTime, notes

How it maps:

    users.name, userId    -> a user with a U ID (U007) whose team an admin picks on
                             their first card scan, or straight into the
                             sections given with --sections;
                             the legacy userId is kept in users.legacy_id
    users.rfidKey         -> a card (see nfc_login.legacy.rfid)
    users.hours           -> an adjustment in the active season
    lastLogin/lastLogout  -> one zero-credit session so "last sign in/out" carry over;
                             people logged in at import time stay signed in
    pastseasons           -> one archived season per seasonStartDate, hours as adjustments
    records               -> copied to legacy_records for reference (not counted again,
                             since users.hours / pastseasons.hours already include them)

The legacy database is only read. Users already imported (same legacy userId)
are skipped, so the import can be run again to pick up new legacy users.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Callable

import pymysql
import pymysql.cursors

from nfc_login.db import Database
from nfc_login.db import repository as repo
from nfc_login.legacy.rfid import placeholder_uid, uid_for_legacy_key
from nfc_login.services import ids, timefmt

IMPORT_REASON = "Imported from legacy system"


@dataclass
class ImportSummary:
    users: int = 0
    skipped_users: int = 0
    cards: int = 0
    cards_need_scan: int = 0
    hours_seconds: int = 0
    seasons: list[str] = field(default_factory=list)
    past_rows: int = 0
    records: int = 0
    signed_in: int = 0
    warnings: list[str] = field(default_factory=list)
    created: list[tuple[int, str, str]] = field(default_factory=list)  # legacy id, code, name


class DryRun(Exception):
    pass


def read_legacy(settings: dict) -> dict[str, list[dict]]:
    """Read the three legacy tables (read-only)."""
    conn = pymysql.connect(
        host=settings["host"], port=int(settings.get("port", 3306)),
        user=settings["user"], password=settings["password"],
        database=settings.get("database", "attendance"), charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users ORDER BY userId")
            users = list(cur.fetchall())
            cur.execute("SHOW TABLES LIKE 'pastseasons'")
            past = []
            if cur.fetchone():
                cur.execute("SELECT * FROM pastseasons ORDER BY seasonStartDate, userId")
                past = list(cur.fetchall())
            cur.execute("SHOW TABLES LIKE 'records'")
            records = []
            if cur.fetchone():
                cur.execute("SELECT * FROM records ORDER BY recordId")
                records = list(cur.fetchall())
    finally:
        conn.close()
    return {"users": users, "pastseasons": past, "records": records}


def _valid_time(value) -> datetime | None:
    """Legacy rows use '0000-00-00 00:00:00' for 'never'; PyMySQL returns that as a string."""
    if isinstance(value, datetime):
        return value if value.year > 1970 else None
    if isinstance(value, date):
        return datetime.combine(value, time())
    return None


def _seconds(hours) -> int:
    return int(round(Decimal(str(hours or 0)) * 3600))


class LegacyImporter:
    def __init__(self, db: Database, sections: list[str] | None = None,
                 clock: Callable[[], datetime] = timefmt.now):
        self.db = db
        # Empty: everyone waits in the U holding section until their first scan.
        self.sections = [s.upper() for s in sections or []]
        self.clock = clock

    def run(self, data: dict[str, list[dict]], dry_run: bool = False) -> ImportSummary:
        summary = ImportSummary()
        try:
            with self.db.transaction() as cur:
                self._import(cur, data, summary)
                if dry_run:
                    raise DryRun()
        except DryRun:
            pass
        return summary

    # ------------------------------------------------------------ steps

    def _import(self, cur, data, summary: ImportSummary) -> None:
        now = self.clock()
        season = repo.get_active_season(cur)
        if not season:
            name = str(now.year)
            repo.create_season(cur, name, now)
            season = repo.get_active_season(cur)

        new_ids: dict[int, int] = {}       # legacy userId -> our user id
        for legacy in data["users"]:
            user_id = self._import_user(cur, legacy, season, now, summary)
            if user_id is not None:
                new_ids[int(legacy["userId"])] = user_id

        self._import_past_seasons(cur, data["pastseasons"], new_ids, season, now, summary)

        for record in data["records"]:
            user_id = new_ids.get(int(record["userId"]))
            if user_id is None:
                continue
            cur.execute(
                "INSERT IGNORE INTO legacy_records "
                "(user_id, legacy_record_id, start_time, end_time, notes) "
                "VALUES (%s, %s, %s, %s, %s)",
                (user_id, record["recordId"], _valid_time(record["startTime"]),
                 _valid_time(record["endTime"]), record.get("notes")),
            )
            summary.records += cur.rowcount

    def _import_user(self, cur, legacy: dict, season: dict, now: datetime,
                     summary: ImportSummary) -> int | None:
        legacy_id = int(legacy["userId"])
        cur.execute("SELECT id FROM users WHERE legacy_id = %s", (legacy_id,))
        if cur.fetchone():
            summary.skipped_users += 1
            return None

        name = (legacy["name"] or f"User {legacy_id}").strip()[:64]
        if repo.get_user_by_name(cur, name):
            name = f"{name[:55]} (old {legacy_id})"
            summary.warnings.append(f"Name already taken, imported as {name!r}")
        section, number = self._next_code(cur)
        user_id = repo.create_user(cur, name, section, number, now)
        cur.execute("UPDATE users SET legacy_id = %s WHERE id = %s", (legacy_id, user_id))
        code = ids.format_code(section, number)
        summary.users += 1
        summary.created.append((legacy_id, code, name))

        key = int(legacy["rfidKey"] or 0)
        if key:
            uid = uid_for_legacy_key(key) or placeholder_uid(key)
            if repo.get_tag(cur, uid) or self._key_taken(cur, key):
                summary.warnings.append(f"Card {key} of {name} already belongs to someone")
            else:
                repo.assign_tag(cur, uid, user_id, now, legacy_key=key)
                summary.cards += 1
                if uid.startswith("LEGACY-"):
                    summary.cards_need_scan += 1

        seconds = _seconds(legacy["hours"])
        if seconds:
            repo.add_adjustment(cur, user_id, season["id"], seconds, IMPORT_REASON, now, "import")
            summary.hours_seconds += seconds

        last_in = _valid_time(legacy.get("lastLogin"))
        last_out = _valid_time(legacy.get("lastLogout"))
        if last_in and legacy.get("loggedIn"):
            repo.open_session(cur, user_id, season["id"], last_in, "import")
            summary.signed_in += 1
        elif last_in and last_out and last_out >= last_in:
            # Zero credit: their hours came in above; this only carries over the dates.
            session_id = repo.open_session(cur, user_id, season["id"], last_in, "import")
            repo.close_session(cur, session_id, last_out, "import", 0)
        return user_id

    def _import_past_seasons(self, cur, rows, new_ids, active, now, summary) -> None:
        dates = sorted({r["seasonStartDate"] for r in rows if r.get("seasonStartDate")})
        for i, start in enumerate(dates):
            season_rows = [r for r in rows if r["seasonStartDate"] == start
                           and int(r["userId"]) in new_ids]
            if not season_rows:
                continue
            name = f"Legacy {start:%Y-%m-%d}" if isinstance(start, date) else f"Legacy {start}"
            existing = repo.get_season_by_name(cur, name)
            if existing:
                season_id = existing["id"]
            else:
                started = _valid_time(start) or now
                ended = _valid_time(dates[i + 1]) if i + 1 < len(dates) else active["started_at"]
                cur.execute(
                    "INSERT INTO seasons (name, started_at, ended_at, is_active) "
                    "VALUES (%s, %s, %s, 0)", (name, started, ended))
                season_id = cur.lastrowid
                summary.seasons.append(name)
            for row in season_rows:
                seconds = _seconds(row["hours"])
                if seconds:
                    repo.add_adjustment(cur, new_ids[int(row["userId"])], season_id, seconds,
                                        IMPORT_REASON, now, "import")
                    summary.past_rows += 1

    # ------------------------------------------------------------ helpers

    def _next_code(self, cur) -> tuple[str, int]:
        """Next free ID, filling the chosen sections in order (A001..A999, then B001...)."""
        if not self.sections:
            return ids.UNSORTED, repo.next_user_number(cur, ids.UNSORTED)
        for section in self.sections:
            number = repo.next_user_number(cur, section)
            if number <= ids.MAX_NUMBER:
                return section, number
        raise ValueError("Not enough free IDs in the chosen sections.")

    @staticmethod
    def _key_taken(cur, key: int) -> bool:
        cur.execute("SELECT 1 FROM tags WHERE legacy_key = %s AND is_active = 1", (key,))
        return cur.fetchone() is not None


def settings_from_legacy_config(path: str) -> dict:
    """Database login from the legacy system's config.json (its read-only 'php' user)."""
    import json
    with open(path) as fh:
        cfg = json.load(fh)["MySQL"]
    return {"host": cfg.get("address", "localhost"), "user": cfg["apiUsername"],
            "password": cfg["apiPassword"], "database": cfg.get("dbName", "attendance")}
