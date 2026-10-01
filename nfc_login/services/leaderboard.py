"""Leaderboard ranking, always computed fresh from the sessions table."""

from __future__ import annotations

from dataclasses import dataclass

from nfc_login.db import repository as repo


@dataclass(frozen=True)
class LeaderboardEntry:
    rank: int
    user_id: int
    username: str
    total_seconds: int


def rank_totals(rows: list[dict]) -> list[LeaderboardEntry]:
    """Sort by total time and assign standard competition ranks (1, 2, 2, 4).

    Users with equal time share a rank; ties are listed alphabetically.
    """
    ordered = sorted(rows, key=lambda r: (-int(r["total_seconds"]), r["username"].lower()))
    entries: list[LeaderboardEntry] = []
    previous_total = None
    rank = 0
    for position, row in enumerate(ordered, start=1):
        total = int(row["total_seconds"])
        if total != previous_total:
            rank = position
            previous_total = total
        entries.append(LeaderboardEntry(rank, int(row["user_id"]), row["username"], total))
    return entries


def leaderboard(cur, season_id: int) -> list[LeaderboardEntry]:
    return rank_totals(repo.season_totals(cur, season_id))


def find_entry(entries: list[LeaderboardEntry], user_id: int) -> LeaderboardEntry | None:
    return next((e for e in entries if e.user_id == user_id), None)
