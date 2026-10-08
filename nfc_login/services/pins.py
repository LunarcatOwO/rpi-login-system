# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""PIN hashing for user and admin keypad PINs (PBKDF2-SHA256, stdlib only)."""

from __future__ import annotations

import hashlib
import hmac
import secrets

# PINs are never stored, only a slow salted hash of them. With only 10^4-10^8
# possible PINs, a slow hash makes guessing from a stolen database take far longer.
ITERATIONS = 100_000


def hash_pin(pin: str) -> str:
    """'1234' -> 'pbkdf2_sha256$iterations$salt$hash', the text saved in the database."""
    if not pin.isdigit() or not 4 <= len(pin) <= 8:
        raise ValueError("PIN must be 4 to 8 digits")
    salt = secrets.token_bytes(16)  # random per PIN, so equal PINs hash differently
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, stored: str | None) -> bool:
    """Hash the typed PIN the same way and compare it with the stored hash."""
    if not stored or not pin:
        return False
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt_hex), int(iterations))
    # compare_digest takes the same time whether or not the first digits match.
    return hmac.compare_digest(digest.hex(), digest_hex)
