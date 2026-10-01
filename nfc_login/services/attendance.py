"""Sign-in / sign-out logic and per-user stats.

Every scan toggles the user: signed out -> signed in, signed in -> signed out.
Time is credited only from timestamps stored in MariaDB; nothing read from a
card is ever used to compute hours.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from nfc_login.db import Database
from nfc_login.db import repository as repo
from nfc_login.services import leaderboard as lb
from nfc_login.services import timefmt
from nfc_login.services.pins import verify_pin

SIGNED_IN = "signed_in"
SIGNED_OUT = "signed_out"
IGNORED = "ignored"


class AttendanceError(Exception):
    """A problem that should be shown to the person at the kiosk."""


class WrongPinError(AttendanceError):
    pass


@dataclass
class UserStats:
    user_id: int
    username: str
    season_name: str
    total_seconds: int
    rank: int | None
    ranked_users: int
    last_sign_in: datetime | None
    last_sign_out: datetime | None
    signed_in: bool

    @property
    def hours_minutes(self) -> tuple[int, int]:
        return timefmt.split_hours_minutes(self.total_seconds)

    @property
    def total_text(self) -> str:
        return timefmt.format_duration(self.total_seconds)


@dataclass
class ScanResult:
    action: str                    # signed_in | signed_out | ignored
    stats: UserStats
    session_seconds: int = 0       # length of the session just closed
    notes: list[str] = field(default_factory=list)


class AttendanceService:
    def __init__(
        self,
        db: Database,
        min_scan_interval_seconds: int = 10,
        max_session_hours: float = 12,
        clock: Callable[[], datetime] = timefmt.now,
    ):
        self.db = db
        self.min_interval = timedelta(seconds=min_scan_interval_seconds)
        self.max_session = timedelta(hours=max_session_hours)
        self.clock = clock

    # ------------------------------------------------------------ lookups

    def user_for_tag(self, uid: str) -> dict:
        with self.db.transaction() as cur:
            tag = repo.get_tag(cur, uid)
            if not tag or not tag["is_active"]:
                raise AttendanceError("Card not registered. Ask an admin to enroll it.")
            user = repo.get_user(cur, tag["user_id"])
        if not user or not user["is_active"]:
            raise AttendanceError("This card's user is deactivated.")
        return user

    def check_keypad_login(self, user_id: int, pin: str) -> dict:
        with self.db.transaction() as cur:
            user = repo.get_user(cur, user_id)
        if not user or not user["is_active"]:
            raise AttendanceError(f"No active user with ID {user_id}.")
        if not user["pin_hash"]:
            raise AttendanceError("No PIN set for this user. Use your card.")
        if not verify_pin(pin, user["pin_hash"]):
            raise WrongPinError("Wrong PIN.")
        return user

    # ------------------------------------------------------------ scanning

    def scan_tag(self, uid: str) -> ScanResult:
        user = self.user_for_tag(uid)
        return self.toggle(user["id"], method="card")

    def toggle(self, user_id: int, method: str) -> ScanResult:
        """Sign the user in or out, whichever applies."""
        now = self.clock()
        notes: list[str] = []
        session_seconds = 0
        with self.db.transaction() as cur:
            # Lock the user row so two scans can't race each other.
            user = repo.get_user(cur, user_id, for_update=True)
            if not user or not user["is_active"]:
                raise AttendanceError(f"No active user with ID {user_id}.")
            season = repo.get_active_season(cur)
            if not season:
                raise AttendanceError("No active season. Run: nfc_login.admin season new")

            open_session = repo.get_open_session(cur, user_id)
            if open_session:
                elapsed = now - open_session["sign_in_at"]
                if elapsed < self.min_interval:
                    action = IGNORED
                    notes.append("Already signed in a moment ago.")
                elif elapsed > self.max_session:
                    # Forgot to sign out last time: no credit, start a fresh session.
                    repo.close_session(cur, open_session["id"], now, "timeout", 0)
                    repo.open_session(cur, user_id, season["id"], now, method)
                    action = SIGNED_IN
                    notes.append(
                        "Previous session was never signed out and earned no time."
                    )
                else:
                    session_seconds = int(elapsed.total_seconds())
                    repo.close_session(cur, open_session["id"], now, method, session_seconds)
                    action = SIGNED_OUT
            else:
                last_out = repo.get_last_sign_out(cur, user_id)
                if last_out and now - last_out < self.min_interval:
                    action = IGNORED
                    notes.append("Already signed out a moment ago.")
                else:
                    repo.open_session(cur, user_id, season["id"], now, method)
                    action = SIGNED_IN

        stats = self.user_stats(user_id)
        return ScanResult(action, stats, session_seconds, notes)

    # ------------------------------------------------------------ stats

    def user_stats(self, user_id: int) -> UserStats:
        with self.db.transaction() as cur:
            user = repo.get_user(cur, user_id)
            if not user:
                raise AttendanceError(f"No user with ID {user_id}.")
            season = repo.get_active_season(cur)
            entries = lb.leaderboard(cur, season["id"]) if season else []
            last = repo.get_last_session(cur, user_id)
            last_out = repo.get_last_sign_out(cur, user_id)
        entry = lb.find_entry(entries, user_id)
        return UserStats(
            user_id=user_id,
            username=user["username"],
            season_name=season["name"] if season else "-",
            total_seconds=entry.total_seconds if entry else 0,
            rank=entry.rank if entry else None,
            ranked_users=len(entries),
            last_sign_in=last["sign_in_at"] if last else None,
            last_sign_out=last_out,
            signed_in=bool(last and last["sign_out_at"] is None),
        )

    def active_season_name(self) -> str:
        with self.db.transaction() as cur:
            season = repo.get_active_season(cur)
        return season["name"] if season else "-"

    def leaderboard(self, limit: int | None = None) -> list[lb.LeaderboardEntry]:
        with self.db.transaction() as cur:
            season = repo.get_active_season(cur)
            entries = lb.leaderboard(cur, season["id"]) if season else []
        return entries[:limit] if limit else entries

    def currently_signed_in(self) -> list[dict]:
        with self.db.transaction() as cur:
            return repo.list_open_sessions(cur)

    # ------------------------------------------------------------ housekeeping

    def sign_out_everyone(self, method: str = "admin") -> int:
        """Close every open session, crediting time (capped sessions get 0)."""
        now = self.clock()
        with self.db.transaction() as cur:
            sessions = repo.list_open_sessions(cur)
            for session in sessions:
                credit = credited_seconds(session["sign_in_at"], now, self.max_session)
                repo.close_session(cur, session["id"], now, method, credit)
        return len(sessions)

    def close_stale_sessions(self) -> int:
        """Close sessions older than max_session_hours with no credit (for cron)."""
        now = self.clock()
        closed = 0
        with self.db.transaction() as cur:
            for session in repo.list_open_sessions(cur):
                if now - session["sign_in_at"] > self.max_session:
                    repo.close_session(cur, session["id"], now, "timeout", 0)
                    closed += 1
        return closed


def credited_seconds(sign_in_at: datetime, now: datetime, max_session: timedelta) -> int:
    """Seconds to credit for a session closed by the system rather than a scan."""
    elapsed = now - sign_in_at
    if elapsed > max_session:
        return 0
    return max(0, int(elapsed.total_seconds()))
