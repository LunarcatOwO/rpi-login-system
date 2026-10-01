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
    db: Database
    attendance: AttendanceService
    seasons: SeasonService
    users: UserService


def build_services(config: Config) -> Services:
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
