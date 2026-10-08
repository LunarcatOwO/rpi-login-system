# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Shared fixtures.

Database tests need a MariaDB server the test user can create databases on.
Point them at one with environment variables, e.g.:

    NFC_LOGIN_TEST_DB_USER=nfc NFC_LOGIN_TEST_DB_PASSWORD=secret pytest

Without NFC_LOGIN_TEST_DB_USER those tests are skipped. The tests drop and
recreate their database (nfc_login_test); set NFC_LOGIN_TEST_DB_NAME to use
another, e.g. to run two test runs at once.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from nfc_login.db import Database
from nfc_login.services.attendance import AttendanceService
from nfc_login.services.seasons import SeasonService
from nfc_login.services.users import UserService


class FakeClock:
    def __init__(self, start=datetime(2026, 9, 1, 15, 0, 0)):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def db():
    user = os.environ.get("NFC_LOGIN_TEST_DB_USER")
    if not user:
        pytest.skip("set NFC_LOGIN_TEST_DB_USER to run database tests")
    settings = {
        "host": os.environ.get("NFC_LOGIN_TEST_DB_HOST", "localhost"),
        "port": int(os.environ.get("NFC_LOGIN_TEST_DB_PORT", "3306")),
        "user": user,
        "password": os.environ.get("NFC_LOGIN_TEST_DB_PASSWORD", ""),
        "name": os.environ.get("NFC_LOGIN_TEST_DB_NAME", "nfc_login_test"),
    }
    database = Database(settings)
    conn = database.connect(with_database=False)
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS `{settings['name']}`")
    conn.close()
    database.create_database()
    database.apply_schema()
    return database


@pytest.fixture
def services(db, clock, tmp_path):
    attendance = AttendanceService(db, min_scan_interval_seconds=10, max_session_hours=12,
                                   clock=clock)
    seasons = SeasonService(db, archive_dir=tmp_path / "archive", max_session_hours=12,
                            clock=clock)
    users = UserService(db, clock=clock)
    seasons.ensure_active()
    return attendance, seasons, users
