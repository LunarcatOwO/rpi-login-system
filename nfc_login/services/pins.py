"""PIN hashing for user and admin keypad PINs (PBKDF2-SHA256, stdlib only)."""

from __future__ import annotations

import hashlib
import hmac
import secrets

ITERATIONS = 100_000


def hash_pin(pin: str) -> str:
    if not pin.isdigit() or not 4 <= len(pin) <= 8:
        raise ValueError("PIN must be 4 to 8 digits")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, stored: str | None) -> bool:
    if not stored or not pin:
        return False
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), digest_hex)
