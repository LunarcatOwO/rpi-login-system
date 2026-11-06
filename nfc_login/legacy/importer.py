# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Import users, cards and hours from the legacy attendance database (read only)."""

# Legacy tables (github.com/aesom-e/attendance) and where each goes here:
#   users.name, userId     -> a user with a U ID (U007) until they pick a team on
#                             their first scan, or a team given with --sections
#   users.rfidKey          -> a card (see rfid.py)
#   users.hours            -> an hours adjustment in the active season, or with
#                             new_season set, in a finished "Legacy 2025-2026"
#                             season while a fresh, empty season starts
#   lastLogin / lastLogout -> one zero-credit session so "last sign in/out" carry over
#   pastseasons            -> one archived season per start date, hours as adjustments
#   records                -> copied to legacy_records for reference only (their time
#                             is already in users.hours, so it isn't counted twice)
# Users already imported (same legacy userId) are skipped, so it can run again.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
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
    """Counts printed at the end of an import."""

    users: int = 0
    skipped_users: int = 0
    cards: int = 0
    cards_need_scan: int = 0
    hours_seconds: int = 0
    seasons: list[str] = field(default_factory=list)
    hours_season: str = ""      # where users.hours went
    active_season: str = ""
    past_rows: int = 0
    records: int = 0
    signed_in: int = 0
    warnings: list[str] = field(default_factory=list)
    created: list[tuple[int, str, str]] = field(default_factory=list)  # legacy id, code, name


class DryRun(Exception):
    """Raised inside the transaction to roll a --dry-run back."""


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
            # Columns are named one by one with an alias because real legacy
            # databases spell them inconsistently (records has recordID, userID,
            # starttime). SQL ignores the case; the dict keys we get back don't.
            cur.execute("SELECT userId AS userId, name AS name, hours AS hours, "
                        "rfidKey AS rfidKey, loggedIn AS loggedIn, lastLogin AS lastLogin, "
                        "lastLogout AS lastLogout FROM users ORDER BY userId")
            users = list(cur.fetchall())
            # Older copies of the legacy system may not have these two tables.
            cur.execute("SHOW TABLES LIKE 'pastseasons'")
            past = []
            if cur.fetchone():
                cur.execute("SELECT userId AS userId, hours AS hours, "
                            "seasonStartDate AS seasonStartDate FROM pastseasons "
                            "ORDER BY seasonStartDate, userId")
                past = list(cur.fetchall())
            cur.execute("SHOW TABLES LIKE 'records'")
            records = []
            if cur.fetchone():
                cur.execute("SELECT recordId AS recordId, userId AS userId, "
                            "startTime AS startTime, endTime AS endTime, notes AS notes "
                            "FROM records ORDER BY recordId")
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


def school_year(day: date) -> str:
    """School season a date falls in: Aug-Dec 2026 and Jan-Jul 2027 are both 2026-2027."""
    start = day.year if day.month >= 8 else day.year - 1
    return f"{start}-{start + 1}"


def _seconds(hours) -> int:
    """Legacy DECIMAL hours (e.g. 12.50) -> whole seconds; Decimal avoids float rounding."""
    return int(round(Decimal(str(hours or 0)) * 3600))


class LegacyImporter:
    """Copies the legacy data in one transaction: all of it goes in, or none."""

    def __init__(self, db: Database, sections: list[str] | None = None,
                 clock: Callable[[], datetime] = timefmt.now, new_season: str | None = None):
        self.db = db
        # Set: the old hours all go into a finished legacy season and this
        # season (e.g. "2026-2027") starts empty. None: they count in the active season.
        self.new_season = new_season
        # Empty: everyone waits in the U holding section until their first scan.
        self.sections = [s.upper() for s in sections or []]
        self.clock = clock

    def run(self, data: dict[str, list[dict]], dry_run: bool = False) -> ImportSummary:
        summary = ImportSummary()
        try:
            with self.db.transaction() as cur:
                self._import(cur, data, summary)
                if dry_run:
                    raise DryRun()  # rolls everything back but keeps the summary
        except DryRun:
            pass
        return summary

    # ------------------------------------------------------------ steps

    def _import(self, cur, data, summary: ImportSummary) -> None:
        now = self.clock()
        # Seasons first, oldest to newest, so they list in the old system's order.
        resets = sorted({d for d in (_valid_time(r.get("seasonStartDate"))
                                     for r in data["pastseasons"]) if d})
        past = self._past_seasons(cur, resets, data, summary)
        if self.new_season:
            # The old system's current hours become a finished season too.
            # Named by when people last signed in, since it ends today.
            last_seen = [t for u in data["users"] for k in ("lastLogin", "lastLogout")
                         if (t := _valid_time(u.get(k)))]
            started = resets[-1] if resets else now
            season = self._finished_season(cur, started, now, summary,
                                           max(last_seen, default=now))
            active = self._start_new_season(cur, now)
        else:
            active = repo.get_active_season(cur)
            if not active:  # fresh database: start a season named after this year
                repo.create_season(cur, str(now.year), now)
                active = repo.get_active_season(cur)
            season = active
        summary.hours_season, summary.active_season = season["name"], active["name"]

        new_ids: dict[int, int] = {}       # legacy userId -> our user id
        for legacy in data["users"]:
            user_id = self._import_user(cur, legacy, season, active, now, summary)
            if user_id is not None:
                new_ids[int(legacy["userId"])] = user_id

        for row in data["pastseasons"]:
            reset = _valid_time(row.get("seasonStartDate"))
            user_id = new_ids.get(int(row["userId"]))
            seconds = _seconds(row["hours"])
            if reset in past and user_id and seconds:
                repo.add_adjustment(cur, user_id, past[reset]["id"], seconds,
                                    IMPORT_REASON, now, "import")
                summary.past_rows += 1

        for record in data["records"]:
            user_id = new_ids.get(int(record["userId"]))
            if user_id is None:
                continue
            # INSERT IGNORE skips records already copied by an earlier run.
            cur.execute(
                "INSERT IGNORE INTO legacy_records "
                "(user_id, legacy_record_id, start_time, end_time, notes) "
                "VALUES (%s, %s, %s, %s, %s)",
                (user_id, record["recordId"], _valid_time(record["startTime"]),
                 _valid_time(record["endTime"]), record.get("notes")),
            )
            summary.records += cur.rowcount

    def _import_user(self, cur, legacy: dict, season: dict, active: dict, now: datetime,
                     summary: ImportSummary) -> int | None:
        """Create one legacy user here with a fresh ID; None if already imported."""
        legacy_id = int(legacy["userId"])
        cur.execute("SELECT id FROM users WHERE legacy_id = %s", (legacy_id,))
        if cur.fetchone():
            summary.skipped_users += 1
            return None

        name = (legacy["name"] or f"User {legacy_id}").strip()[:64]  # column holds 64
        if repo.get_user_by_name(cur, name):  # names are unique here
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
            # A 4-byte card's full UID can be worked out now; a 7-byte card gets a
            # placeholder UID that its first real scan replaces.
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
        if last_in and legacy.get("loggedIn"):  # here right now: stays signed in
            repo.open_session(cur, user_id, active["id"], last_in, "import")
            summary.signed_in += 1
        elif last_in and last_out and last_out >= last_in:
            # Zero credit: their hours came in above; this only carries over the dates.
            session_id = repo.open_session(cur, user_id, season["id"], last_in, "import")
            repo.close_session(cur, session_id, last_out, "import", 0)
        return user_id

    # ------------------------------------------------------------ legacy seasons
    # The old system's "new season" copied everyone's hours into pastseasons,
    # labelled with the date typed in: the day the NEXT season started. So each
    # label is the END of the season its rows belong to (checked against a real
    # dump: the 2026-01-01 rows match the visit log from 2024-11 to 2025-06).

    def _past_seasons(self, cur, resets, data, summary) -> dict:
        """One finished season per old reset date, oldest first: {reset date: season}."""
        used = {_valid_time(r.get("seasonStartDate")) for r in data["pastseasons"]}
        first_record = min((t for t in (_valid_time(r["startTime"]) for r in data["records"])
                            if t), default=None)
        seasons = {}
        for i, ended in enumerate(resets):
            # Begins at the reset before it; the oldest one's start was never saved.
            started = resets[i - 1] if i else min(first_record or ended, ended)
            if ended in used:
                seasons[ended] = self._finished_season(cur, started, ended, summary)
        return seasons

    def _finished_season(self, cur, started: datetime, ended: datetime, summary,
                         active_on: datetime | None = None) -> dict:
        """Find or make a finished season named after its school year (Legacy 2024-2025)."""
        # Old resets were done late (November, January), so by default name it
        # by the school year half a year before it ended.
        name = "Legacy " + school_year((active_on or ended - timedelta(days=183)).date())
        existing = repo.get_season_by_name(cur, name)
        # Made by an earlier run (the current-hours season ended "now" back then).
        if existing and (existing["ended_at"] == ended or active_on is not None):
            return existing
        if existing:  # two old resets in one school year
            name = f"{name} (to {ended:%Y-%m-%d})"
            existing = repo.get_season_by_name(cur, name)
            if existing:
                return existing
        cur.execute("INSERT INTO seasons (name, started_at, ended_at, is_active) "
                    "VALUES (%s, %s, %s, 0)", (name, started, ended))
        summary.seasons.append(name)
        return repo.get_season_by_name(cur, name)

    # ------------------------------------------------------------ new season

    def _start_new_season(self, cur, now: datetime) -> dict:
        """Make self.new_season the active season, empty, starting now."""
        active = repo.get_active_season(cur)
        if active and active["name"] == self.new_season:
            return active  # an earlier run already set it up
        if repo.get_season_by_name(cur, self.new_season):
            raise ValueError(f"A finished season is already called {self.new_season!r}.")
        if active and self._season_is_empty(cur, active["id"]):
            # The empty placeholder init-db made (named after the year): drop it.
            cur.execute("DELETE FROM seasons WHERE id = %s", (active["id"],))
        elif active:
            repo.end_season(cur, active["id"], now)
        repo.create_season(cur, self.new_season, now)
        return repo.get_active_season(cur)

    @staticmethod
    def _season_is_empty(cur, season_id: int) -> bool:
        cur.execute("SELECT (SELECT COUNT(*) FROM sessions WHERE season_id = %s) + "
                    "(SELECT COUNT(*) FROM adjustments WHERE season_id = %s) AS n",
                    (season_id, season_id))
        return cur.fetchone()["n"] == 0

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
        """True if an active card already has this legacy number."""
        cur.execute("SELECT 1 FROM tags WHERE legacy_key = %s AND is_active = 1", (key,))
        return cur.fetchone() is not None


def settings_from_legacy_config(path: str) -> dict:
    """Database login from the legacy system's config.json (its read-only 'php' user)."""
    import json
    with open(path) as fh:
        cfg = json.load(fh)["MySQL"]
    return {"host": cfg.get("address", "localhost"), "user": cfg["apiUsername"],
            "password": cfg["apiPassword"], "database": cfg.get("dbName", "attendance")}
