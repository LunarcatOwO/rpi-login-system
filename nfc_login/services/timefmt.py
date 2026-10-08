# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Formatting helpers for durations and timestamps."""

from __future__ import annotations

from datetime import datetime


def split_hours_minutes(total_seconds: int) -> tuple[int, int]:
    """Whole hours and leftover whole minutes (seconds are dropped)."""
    total_minutes = max(0, int(total_seconds)) // 60
    return total_minutes // 60, total_minutes % 60


def format_duration(total_seconds: int) -> str:
    hours, minutes = split_hours_minutes(total_seconds)
    return f"{hours}h {minutes:02d}m"


def format_timestamp(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "never"


def now() -> datetime:
    """Current local time, truncated to whole seconds to match DATETIME columns."""
    return datetime.now().replace(microsecond=0)
