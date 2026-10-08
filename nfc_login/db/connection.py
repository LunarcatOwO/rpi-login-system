# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""MariaDB connection handling.

Each unit of work opens its own short-lived connection. On a Pi talking to a
local MariaDB that is cheap, and it means the kiosk never trips over a
connection the server timed out overnight.
"""

from __future__ import annotations

from contextlib import contextmanager
from importlib import resources
from typing import Iterator

import pymysql
import pymysql.cursors


class Database:
    def __init__(self, settings: dict):
        self.settings = settings

    def connect(self, with_database: bool = True) -> pymysql.connections.Connection:
        kwargs = dict(
            host=self.settings["host"],
            port=int(self.settings["port"]),
            user=self.settings["user"],
            password=self.settings["password"],
            charset="utf8mb4",
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
        if with_database:
            kwargs["database"] = self.settings["name"]
        return pymysql.connect(**kwargs)

    @contextmanager
    def transaction(self) -> Iterator[pymysql.cursors.DictCursor]:
        """Yield a cursor; commit on success, roll back on any error."""
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                yield cur
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_database(self) -> None:
        """Create the database itself if the configured user is allowed to."""
        conn = self.connect(with_database=False)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.settings['name']}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            conn.commit()
        finally:
            conn.close()

    def apply_schema(self) -> None:
        sql = resources.files("nfc_login.db").joinpath("schema.sql").read_text()
        statements = [s.strip() for s in _strip_comments(sql).split(";") if s.strip()]
        with self.transaction() as cur:
            for statement in statements:
                cur.execute(statement)


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
