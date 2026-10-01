"""User and card management, shared by the admin CLI and the kiosk's admin menu."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import pymysql

from nfc_login.db import Database
from nfc_login.db import repository as repo
from nfc_login.services import timefmt
from nfc_login.services.pins import hash_pin, verify_pin

ADMIN_PIN_SETTING = "admin_pin_hash"


class UserError(Exception):
    pass


class UserService:
    def __init__(self, db: Database, clock: Callable[[], datetime] = timefmt.now):
        self.db = db
        self.clock = clock

    def add(self, username: str, pin: str | None = None) -> int:
        username = username.strip()
        if not username:
            raise UserError("Username can't be empty.")
        if len(username) > 64:
            raise UserError("Username must be 64 characters or fewer.")
        pin_hash = hash_pin(pin) if pin else None
        try:
            with self.db.transaction() as cur:
                return repo.create_user(cur, username, self.clock(), pin_hash)
        except pymysql.err.IntegrityError:
            raise UserError(f"Username {username!r} is already taken.") from None

    def get(self, user_id: int) -> dict:
        with self.db.transaction() as cur:
            user = repo.get_user(cur, user_id)
        if not user:
            raise UserError(f"No user with ID {user_id}.")
        return user

    def list(self, include_inactive: bool = False) -> list[dict]:
        with self.db.transaction() as cur:
            return repo.list_users(cur, include_inactive)

    def set_pin(self, user_id: int, pin: str | None) -> None:
        self.get(user_id)
        with self.db.transaction() as cur:
            repo.set_user_pin(cur, user_id, hash_pin(pin) if pin else None)

    def rename(self, user_id: int, username: str) -> None:
        self.get(user_id)
        try:
            with self.db.transaction() as cur:
                repo.rename_user(cur, user_id, username.strip())
        except pymysql.err.IntegrityError:
            raise UserError(f"Username {username!r} is already taken.") from None

    def set_active(self, user_id: int, active: bool) -> None:
        self.get(user_id)
        with self.db.transaction() as cur:
            repo.set_user_active(cur, user_id, active)

    # ------------------------------------------------------------ cards

    def enroll_tag(self, uid: str, user_id: int) -> dict:
        """Link a card UID to a user. Refuses a card that already belongs to someone else."""
        uid = uid.strip().upper()
        with self.db.transaction() as cur:
            user = repo.get_user(cur, user_id)
            if not user or not user["is_active"]:
                raise UserError(f"No active user with ID {user_id}.")
            existing = repo.get_tag(cur, uid)
            if existing and existing["is_active"] and existing["user_id"] != user_id:
                owner = repo.get_user(cur, existing["user_id"])
                raise UserError(
                    f"Card already belongs to {owner['username']} (ID {owner['id']}). "
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
