"""SQL queries. Every function takes an open cursor from ``Database.transaction``.

Keeping the SQL here means the services above only deal in plain Python
values and the queries are easy to find and review in one place.
"""

from __future__ import annotations

from datetime import datetime

from nfc_login.legacy.rfid import legacy_key_for_uid
from nfc_login.services.ids import with_code

# ---------------------------------------------------------------- users


def create_user(cur, username: str, section: str, number: int, now: datetime,
                pin_hash: str | None = None) -> int:
    cur.execute(
        "INSERT INTO users (section, number, username, pin_hash, created_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (section, number, username, pin_hash, now),
    )
    return cur.lastrowid


def next_user_number(cur, section: str) -> int:
    """Lowest unused number in a section (fills gaps left by removed IDs)."""
    cur.execute("SELECT number FROM users WHERE section = %s ORDER BY number FOR UPDATE",
                (section,))
    expected = 1
    for row in cur.fetchall():
        if row["number"] != expected:
            break
        expected += 1
    return expected


def get_user(cur, user_id: int, for_update: bool = False) -> dict | None:
    sql = "SELECT * FROM users WHERE id = %s"
    if for_update:
        sql += " FOR UPDATE"
    cur.execute(sql, (user_id,))
    return with_code(cur.fetchone())


def get_user_by_code(cur, section: str, number: int) -> dict | None:
    cur.execute("SELECT * FROM users WHERE section = %s AND number = %s", (section, number))
    return with_code(cur.fetchone())


def get_user_by_name(cur, username: str) -> dict | None:
    cur.execute("SELECT * FROM users WHERE username = %s", (username,))
    return with_code(cur.fetchone())


def list_users(cur, include_inactive: bool = False) -> list[dict]:
    sql = "SELECT * FROM users"
    if not include_inactive:
        sql += " WHERE is_active = 1"
    cur.execute(sql + " ORDER BY section, number")
    return [with_code(r) for r in cur.fetchall()]


def set_user_pin(cur, user_id: int, pin_hash: str | None) -> None:
    cur.execute("UPDATE users SET pin_hash = %s WHERE id = %s", (pin_hash, user_id))


def set_user_active(cur, user_id: int, active: bool) -> None:
    cur.execute("UPDATE users SET is_active = %s WHERE id = %s", (int(active), user_id))


def rename_user(cur, user_id: int, username: str) -> None:
    cur.execute("UPDATE users SET username = %s WHERE id = %s", (username, user_id))


# ---------------------------------------------------------------- tags


def get_tag(cur, uid: str) -> dict | None:
    cur.execute("SELECT * FROM tags WHERE uid = %s", (uid,))
    return cur.fetchone()


def find_tag(cur, uid: str) -> dict | None:
    """Look a scanned card up by UID, falling back to its legacy card number.

    The fallback lets cards imported from the legacy system match a scan (its
    RC522 reader only saw part of a 7-byte UID).
    A card matched that way has its stored UID updated to the scanned one.
    """
    tag = get_tag(cur, uid)
    if tag:
        return tag
    key = legacy_key_for_uid(uid)
    if key is None:
        return None
    cur.execute("SELECT * FROM tags WHERE legacy_key = %s AND is_active = 1 "
                "ORDER BY enrolled_at DESC LIMIT 1", (key,))
    tag = cur.fetchone()
    if tag and tag["uid"] != uid:
        cur.execute("UPDATE tags SET uid = %s WHERE uid = %s", (uid, tag["uid"]))
        tag["uid"] = uid
    return tag


def assign_tag(cur, uid: str, user_id: int, now: datetime,
               legacy_key: int | None = None) -> None:
    if legacy_key is None:
        legacy_key = legacy_key_for_uid(uid)
    cur.execute(
        "INSERT INTO tags (uid, user_id, enrolled_at, is_active, legacy_key) "
        "VALUES (%s, %s, %s, 1, %s) "
        "ON DUPLICATE KEY UPDATE user_id = VALUES(user_id), "
        "enrolled_at = VALUES(enrolled_at), is_active = 1, legacy_key = VALUES(legacy_key)",
        (uid, user_id, now, legacy_key),
    )


def deactivate_tag(cur, uid: str) -> int:
    cur.execute("UPDATE tags SET is_active = 0 WHERE uid = %s", (uid,))
    return cur.rowcount


def list_tags(cur) -> list[dict]:
    cur.execute(
        "SELECT t.*, u.username, u.section, u.number FROM tags t "
        "JOIN users u ON u.id = t.user_id ORDER BY u.section, u.number, t.enrolled_at"
    )
    return [with_code(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- seasons


def get_active_season(cur, for_update: bool = False) -> dict | None:
    sql = "SELECT * FROM seasons WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
    if for_update:
        sql += " FOR UPDATE"
    cur.execute(sql)
    return cur.fetchone()


def get_season_by_name(cur, name: str) -> dict | None:
    cur.execute("SELECT * FROM seasons WHERE name = %s", (name,))
    return cur.fetchone()


def list_seasons(cur) -> list[dict]:
    cur.execute("SELECT * FROM seasons ORDER BY id")
    return list(cur.fetchall())


def create_season(cur, name: str, now: datetime) -> int:
    cur.execute(
        "INSERT INTO seasons (name, started_at, is_active) VALUES (%s, %s, 1)",
        (name, now),
    )
    return cur.lastrowid


def end_season(cur, season_id: int, now: datetime) -> None:
    cur.execute(
        "UPDATE seasons SET is_active = 0, ended_at = %s WHERE id = %s",
        (now, season_id),
    )


# ---------------------------------------------------------------- sessions


def get_open_session(cur, user_id: int) -> dict | None:
    cur.execute(
        "SELECT * FROM sessions WHERE user_id = %s AND sign_out_at IS NULL "
        "ORDER BY sign_in_at DESC LIMIT 1",
        (user_id,),
    )
    return cur.fetchone()


def list_open_sessions(cur) -> list[dict]:
    cur.execute(
        "SELECT s.*, u.username, u.section, u.number FROM sessions s "
        "JOIN users u ON u.id = s.user_id "
        # u.number breaks ties, so pages of the list never overlap or skip anyone.
        "WHERE s.sign_out_at IS NULL ORDER BY u.section, s.sign_in_at, u.number, s.id"
    )
    return [with_code(r) for r in cur.fetchall()]


def get_last_session(cur, user_id: int) -> dict | None:
    cur.execute(
        "SELECT * FROM sessions WHERE user_id = %s ORDER BY sign_in_at DESC, id DESC LIMIT 1",
        (user_id,),
    )
    return cur.fetchone()


def get_last_sign_out(cur, user_id: int) -> datetime | None:
    cur.execute(
        "SELECT MAX(sign_out_at) AS last_out FROM sessions WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    return row["last_out"] if row else None


def open_session(cur, user_id: int, season_id: int, now: datetime, method: str) -> int:
    cur.execute(
        "INSERT INTO sessions (user_id, season_id, sign_in_at, sign_in_method) "
        "VALUES (%s, %s, %s, %s)",
        (user_id, season_id, now, method),
    )
    return cur.lastrowid


def close_session(cur, session_id: int, now: datetime, method: str, credited_seconds: int) -> None:
    cur.execute(
        "UPDATE sessions SET sign_out_at = %s, sign_out_method = %s, credited_seconds = %s "
        "WHERE id = %s AND sign_out_at IS NULL",
        (now, method, credited_seconds, session_id),
    )


def season_totals(cur, season_id: int) -> list[dict]:
    """Season time per active user: session time plus admin adjustments."""
    cur.execute(
        "SELECT u.id AS user_id, u.username, u.section, u.number, "
        "  COALESCE((SELECT SUM(s.credited_seconds) FROM sessions s "
        "            WHERE s.user_id = u.id AND s.season_id = %s), 0) AS session_seconds, "
        "  COALESCE((SELECT SUM(a.seconds) FROM adjustments a "
        "            WHERE a.user_id = u.id AND a.season_id = %s), 0) AS adjustment_seconds "
        "FROM users u WHERE u.is_active = 1",
        (season_id, season_id),
    )
    rows = [with_code(r) for r in cur.fetchall()]
    for row in rows:
        row["session_seconds"] = int(row["session_seconds"])
        row["adjustment_seconds"] = int(row["adjustment_seconds"])
        row["total_seconds"] = max(0, row["session_seconds"] + row["adjustment_seconds"])
    return rows


def user_season_total(cur, user_id: int, season_id: int) -> int:
    cur.execute(
        "SELECT COALESCE((SELECT SUM(credited_seconds) FROM sessions "
        "                 WHERE user_id = %s AND season_id = %s), 0) "
        "     + COALESCE((SELECT SUM(seconds) FROM adjustments "
        "                 WHERE user_id = %s AND season_id = %s), 0) AS total",
        (user_id, season_id, user_id, season_id),
    )
    return int(cur.fetchone()["total"])


def season_sessions(cur, season_id: int) -> list[dict]:
    cur.execute(
        "SELECT s.*, u.username, u.section, u.number FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.season_id = %s ORDER BY s.sign_in_at",
        (season_id,),
    )
    return [with_code(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- adjustments


def add_adjustment(cur, user_id: int, season_id: int, seconds: int, reason: str,
                   now: datetime, via: str) -> int:
    cur.execute(
        "INSERT INTO adjustments (user_id, season_id, seconds, reason, created_at, created_via) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, season_id, seconds, reason, now, via),
    )
    return cur.lastrowid


def list_adjustments(cur, season_id: int, limit: int = 50) -> list[dict]:
    cur.execute(
        "SELECT a.*, u.username, u.section, u.number FROM adjustments a "
        "JOIN users u ON u.id = a.user_id WHERE a.season_id = %s "
        "ORDER BY a.created_at DESC, a.id DESC LIMIT %s",
        (season_id, limit),
    )
    return [with_code(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- settings


def get_setting(cur, name: str) -> str | None:
    cur.execute("SELECT value FROM settings WHERE name = %s", (name,))
    row = cur.fetchone()
    return row["value"] if row else None


def set_setting(cur, name: str, value: str) -> None:
    cur.execute(
        "INSERT INTO settings (name, value) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE value = VALUES(value)",
        (name, value),
    )
