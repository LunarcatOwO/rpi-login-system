# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Leaderboard ranking, always computed fresh from the database."""

from __future__ import annotations

from dataclasses import dataclass

from nfc_login.db import repository as repo


@dataclass(frozen=True)
class LeaderboardEntry:
    rank: int
    user_id: int
    code: str
    username: str
    section: str
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
        entries.append(LeaderboardEntry(rank, int(row["user_id"]), row["code"], row["username"],
                                        row["section"], total))
    return entries


def leaderboard(cur, season_id: int) -> list[LeaderboardEntry]:
    return rank_totals(repo.season_totals(cur, season_id))


def find_entry(entries: list[LeaderboardEntry], user_id: int) -> LeaderboardEntry | None:
    return next((e for e in entries if e.user_id == user_id), None)
