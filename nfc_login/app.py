# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Wires the pieces together. Shared by the kiosk and the admin tool."""

from __future__ import annotations

from dataclasses import dataclass

from nfc_login.config import Config
from nfc_login.db import Database
from nfc_login.services.attendance import AttendanceService
from nfc_login.services.seasons import SeasonService
from nfc_login.services.users import UserService


@dataclass
class Services:
    """Every service, sharing one Database."""

    db: Database
    attendance: AttendanceService
    seasons: SeasonService
    users: UserService


def build_services(config: Config) -> Services:
    """Create the services from the config; nothing connects until first used."""
    db = Database(config.database)
    att = config.attendance
    return Services(
        db=db,
        attendance=AttendanceService(
            db,
            min_scan_interval_seconds=att["min_scan_interval_seconds"],
            max_session_hours=att["max_session_hours"],
        ),
        seasons=SeasonService(
            db,
            archive_dir=config.seasons["archive_dir"],
            max_session_hours=att["max_session_hours"],
        ),
        users=UserService(db, config.sections),
    )
