# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""User and card management, shared by the admin CLI, kiosk admin menu and web admin.

Only admins can create users or enroll cards: the kiosk asks for the admin
PIN first, the web page needs an admin login, and the CLI runs on the Pi.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Callable

import pymysql

from nfc_login.config import DEFAULTS
from nfc_login.db import Database
from nfc_login.db import repository as repo
from nfc_login.services import ids, timefmt
from nfc_login.services.pins import hash_pin, verify_pin

ADMIN_PIN_SETTING = "admin_pin_hash"


class UserError(Exception):
    pass


class UserService:
    """Adds, renames and deactivates people, and links NFC cards to them."""

    def __init__(self, db: Database, sections: list[dict] | None = None,
                 clock: Callable[[], datetime] = timefmt.now):
        self.db = db
        self.sections = sections or copy.deepcopy(DEFAULTS["sections"])
        self.clock = clock

    @property
    def section_names(self) -> dict[str, str]:
        return {s["letter"]: s["name"] for s in self.sections}

    def team_name(self, section: str, short: bool = False) -> str:
        """'B' -> 'Impact'. Imported people with no team yet get "No team yet"."""
        for s in self.sections:
            if s["letter"] == section:
                return s.get("short", s["name"]) if short else s["name"]
        return ids.UNSORTED_NAME if section == ids.UNSORTED else section

    def _check_section(self, section: str) -> str:
        section = section.strip().upper()
        if section not in self.section_names:
            choices = ", ".join(f"{k} ({v})" for k, v in self.section_names.items())
            raise UserError(f"Team must be one of {choices}.")
        return section

    def _next_number(self, cur, section: str) -> int:
        number = repo.next_user_number(cur, section)
        if number > ids.MAX_NUMBER:
            raise UserError(f"{self.team_name(section)} is full ({ids.MAX_NUMBER} people).")
        return number

    def add(self, username: str, section: str, pin: str | None = None) -> dict:
        """Create a user in a team; they get the next free ID there (A001, A002...)."""
        username = username.strip()
        if not username:
            raise UserError("Username can't be empty.")
        if len(username) > 64:
            raise UserError("Username must be 64 characters or fewer.")
        section = self._check_section(section)
        pin_hash = hash_pin(pin) if pin else None
        try:
            with self.db.transaction() as cur:
                number = self._next_number(cur, section)
                user_id = repo.create_user(cur, username, section, number, self.clock(), pin_hash)
                return repo.get_user(cur, user_id)
        except pymysql.err.IntegrityError:
            raise UserError(f"Username {username!r} is already taken.") from None

    def move(self, user_id: int, section: str) -> dict:
        """Put a user in another team; they get its next free ID."""
        section = self._check_section(section)
        with self.db.transaction() as cur:
            user = repo.get_user(cur, user_id, for_update=True)
            if not user:
                raise UserError("No user with that ID.")
            if user["section"] == section:
                return user
            number = self._next_number(cur, section)
            cur.execute("UPDATE users SET section = %s, number = %s WHERE id = %s",
                        (section, number, user_id))
            return repo.get_user(cur, user_id)

    def get(self, user_id: int) -> dict:
        with self.db.transaction() as cur:
            user = repo.get_user(cur, user_id)
        if not user:
            raise UserError("No user with that ID.")
        return user

    def get_by_code(self, code: str) -> dict:
        try:
            section, number = ids.parse_code(code)
        except ValueError as exc:
            raise UserError(str(exc)) from None
        with self.db.transaction() as cur:
            user = repo.get_user_by_code(cur, section, number)
        if not user:
            raise UserError(f"No user with ID {ids.format_code(section, number)}.")
        return user

    def list(self, include_inactive: bool = False) -> list[dict]:
        with self.db.transaction() as cur:
            return repo.list_users(cur, include_inactive)

    def set_pin(self, code: str, pin: str | None) -> None:
        user = self.get_by_code(code)
        with self.db.transaction() as cur:
            repo.set_user_pin(cur, user["id"], hash_pin(pin) if pin else None)

    def rename(self, code: str, username: str) -> None:
        user = self.get_by_code(code)
        try:
            with self.db.transaction() as cur:
                repo.rename_user(cur, user["id"], username.strip())
        except pymysql.err.IntegrityError:
            raise UserError(f"Username {username!r} is already taken.") from None

    def set_active(self, code: str, active: bool) -> None:
        user = self.get_by_code(code)
        with self.db.transaction() as cur:
            repo.set_user_active(cur, user["id"], active)

    # ------------------------------------------------------------ cards

    def enroll_tag(self, uid: str, user_id: int) -> dict:
        """Link a card UID to a user. Refuses a card that already belongs to someone else.

        Callers are responsible for checking the person is an admin.
        """
        uid = uid.strip().upper()
        with self.db.transaction() as cur:
            user = repo.get_user(cur, user_id)
            if not user or not user["is_active"]:
                raise UserError("No active user with that ID.")
            existing = repo.find_tag(cur, uid)
            if existing and existing["is_active"] and existing["user_id"] != user_id:
                owner = repo.get_user(cur, existing["user_id"])
                raise UserError(
                    f"Card already belongs to {owner['username']} ({owner['code']}). "
                    "Remove it first."
                )
            repo.assign_tag(cur, uid, user_id, self.clock())
        return user

    def remove_tag(self, uid: str) -> None:
        with self.db.transaction() as cur:
            if not repo.deactivate_tag(cur, uid.strip().upper()):
                raise UserError(f"No card with UID {uid}.")

    def list_tags(self) -> list[dict]:
        with self.db.transaction() as cur:
            return repo.list_tags(cur)

    # ------------------------------------------------------------ admin PIN

    def set_admin_pin(self, pin: str) -> None:
        with self.db.transaction() as cur:
            repo.set_setting(cur, ADMIN_PIN_SETTING, hash_pin(pin))

    def admin_pin_set(self) -> bool:
        with self.db.transaction() as cur:
            return repo.get_setting(cur, ADMIN_PIN_SETTING) is not None

    def admin_pin_hash(self) -> str | None:
        """The stored admin PIN hash: changes whenever the PIN does."""
        with self.db.transaction() as cur:
            return repo.get_setting(cur, ADMIN_PIN_SETTING)

    def check_admin_pin(self, pin: str) -> bool:
        with self.db.transaction() as cur:
            return verify_pin(pin, repo.get_setting(cur, ADMIN_PIN_SETTING))
