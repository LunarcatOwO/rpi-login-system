"""Season management.

A season reset never deletes anything. It:
  1. signs out everyone still signed in (crediting their time to the old season),
  2. marks the old season ended,
  3. writes a CSV snapshot of the old season to the archive folder,
  4. starts a new active season.
Users and their cards carry over; their hours start from zero because hours
are always summed per season.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from nfc_login.db import Database
from nfc_login.db import repository as repo
from nfc_login.services import leaderboard as lb
from nfc_login.services import timefmt
from nfc_login.services.attendance import credited_seconds


class SeasonError(Exception):
    pass


@dataclass
class ResetSummary:
    old_season: str | None
    new_season: str
    signed_out: int
    archive_files: list[Path]


class SeasonService:
    def __init__(
        self,
        db: Database,
        archive_dir: str | Path = "archive",
        max_session_hours: float = 12,
        clock: Callable[[], datetime] = timefmt.now,
    ):
        self.db = db
        self.archive_dir = Path(archive_dir)
        self.max_session = timedelta(hours=max_session_hours)
        self.clock = clock

    def active(self) -> dict | None:
        with self.db.transaction() as cur:
            return repo.get_active_season(cur)

    def list(self) -> list[dict]:
        with self.db.transaction() as cur:
            return repo.list_seasons(cur)

    def ensure_active(self) -> dict:
        """Create a season named after the current year if none is active."""
        with self.db.transaction() as cur:
            season = repo.get_active_season(cur)
            if season:
                return season
            name = self._unique_name(cur, str(self.clock().year))
            repo.create_season(cur, name, self.clock())
            return repo.get_active_season(cur)

    def start_new(self, name: str | None = None) -> ResetSummary:
        now = self.clock()
        with self.db.transaction() as cur:
            name = name or str(now.year)
            if repo.get_season_by_name(cur, name):
                raise SeasonError(f"A season named {name!r} already exists.")

            old = repo.get_active_season(cur, for_update=True)
            signed_out = 0
            if old:
                for session in repo.list_open_sessions(cur):
                    credit = credited_seconds(session["sign_in_at"], now, self.max_session)
                    repo.close_session(cur, session["id"], now, "season_reset", credit)
                    signed_out += 1
                repo.end_season(cur, old["id"], now)
            repo.create_season(cur, name, now)

        files = self.export(old["name"]) if old else []
        return ResetSummary(old["name"] if old else None, name, signed_out, files)

    def leaderboard(self, name: str | None = None) -> tuple[dict, list[lb.LeaderboardEntry]]:
        with self.db.transaction() as cur:
            season = repo.get_season_by_name(cur, name) if name else repo.get_active_season(cur)
            if not season:
                raise SeasonError(f"No season named {name!r}." if name else "No active season.")
            return season, lb.leaderboard(cur, season["id"])

    def export(self, name: str) -> list[Path]:
        """Write <archive>/<season>-leaderboard.csv and <season>-sessions.csv."""
        with self.db.transaction() as cur:
            season = repo.get_season_by_name(cur, name)
            if not season:
                raise SeasonError(f"No season named {name!r}.")
            entries = lb.leaderboard(cur, season["id"])
            sessions = repo.season_sessions(cur, season["id"])
            adjustments = repo.list_adjustments(cur, season["id"], limit=1_000_000)

        self.archive_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        board_path = self.archive_dir / f"{safe}-leaderboard.csv"
        sessions_path = self.archive_dir / f"{safe}-sessions.csv"

        with board_path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["rank", "user_id", "username", "section", "total_seconds",
                             "hours", "minutes"])
            for e in entries:
                hours, minutes = timefmt.split_hours_minutes(e.total_seconds)
                writer.writerow([e.rank, e.code, e.username, e.section, e.total_seconds,
                                 hours, minutes])

        with sessions_path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            columns = ["id", "code", "username", "sign_in_at", "sign_in_method",
                       "sign_out_at", "sign_out_method", "credited_seconds"]
            writer.writerow([c if c != "code" else "user_id" for c in columns])
            for s in sessions:
                writer.writerow([s[c] for c in columns])
            # Admin corrections are listed after the sessions.
            for a in adjustments:
                writer.writerow([f"adj-{a['id']}", a["code"], a["username"], a["created_at"],
                                 f"adjustment ({a['created_via']})", "", a["reason"],
                                 a["seconds"]])

        return [board_path, sessions_path]

    @staticmethod
    def _unique_name(cur, base: str) -> str:
        name, n = base, 2
        while repo.get_season_by_name(cur, name):
            name = f"{base}-{n}"
            n += 1
        return name
