# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""MariaDB connection handling."""

from __future__ import annotations

from contextlib import contextmanager
from importlib import resources
from typing import Iterator

import pymysql
import pymysql.cursors


class Database:
    """Opens a short-lived connection for each unit of work.

    That's cheap against a MariaDB on the same Pi, and the kiosk never trips
    over a connection the server timed out overnight.
    """

    def __init__(self, settings: dict):
        self.settings = settings

    def connect(self, with_database: bool = True) -> pymysql.connections.Connection:
        kwargs = dict(
            host=self.settings["host"],
            port=int(self.settings["port"]),
            user=self.settings["user"],
            password=self.settings["password"],
            charset="utf8mb4",
            autocommit=False,                         # commit only in transaction()
            cursorclass=pymysql.cursors.DictCursor,   # rows come back as dicts
        )
        if with_database:  # False only to create the database itself
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
        """Run schema.sql; every statement is IF NOT EXISTS, so it's safe to repeat."""
        # PyMySQL runs one statement at a time: drop "--" comments, split on ";".
        sql = resources.files("nfc_login.db").joinpath("schema.sql").read_text()
        statements = [s.strip() for s in _strip_comments(sql).split(";") if s.strip()]
        with self.transaction() as cur:
            for statement in statements:
                cur.execute(statement)


def _strip_comments(sql: str) -> str:
    """Remove "-- comment" text from every line of an SQL file."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
