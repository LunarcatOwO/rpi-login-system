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
    def __init__(self, db: Database, sections: list[dict] | None = None,
                 clock: Callable[[], datetime] = timefmt.now):
        self.db = db
        self.sections = sections or copy.deepcopy(DEFAULTS["sections"])
        self.clock = clock

    @property
    def section_names(self) -> dict[str, str]:
        return {s["letter"]: s["name"] for s in self.sections}

    def add(self, username: str, section: str, pin: str | None = None) -> dict:
        """Create a user in a section; they get the next free ID there (A01, A02...)."""
        username = username.strip()
        section = section.strip().upper()
        if not username:
            raise UserError("Username can't be empty.")
        if len(username) > 64:
            raise UserError("Username must be 64 characters or fewer.")
        if section not in self.section_names:
            raise UserError(f"Section must be one of {', '.join(self.section_names)}.")
        pin_hash = hash_pin(pin) if pin else None
        try:
            with self.db.transaction() as cur:
                number = repo.next_user_number(cur, section)
                if number > ids.MAX_NUMBER:
                    raise UserError(f"Section {section} is full ({ids.MAX_NUMBER} users).")
                user_id = repo.create_user(cur, username, section, number, self.clock(), pin_hash)
                return repo.get_user(cur, user_id)
        except pymysql.err.IntegrityError:
            raise UserError(f"Username {username!r} is already taken.") from None

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

    def check_admin_pin(self, pin: str) -> bool:
        with self.db.transaction() as cur:
            return verify_pin(pin, repo.get_setting(cur, ADMIN_PIN_SETTING))
