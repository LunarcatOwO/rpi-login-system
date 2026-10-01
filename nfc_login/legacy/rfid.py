"""Card numbers from the legacy system.

The legacy kiosk read cards with the `mfrc522` Python library's
``SimpleMFRC522``, which turns the 5 bytes from the anticollision step
(4 UID bytes + a BCC check byte) into one integer:

    rfidKey = int.from_bytes(uid0 uid1 uid2 uid3 bcc)    # bcc = uid0^uid1^uid2^uid3

For 7-byte NTAG cards the anticollision step only returns the first cascade
level, which is 0x88 followed by the first 3 UID bytes. So:

    4-byte card 'DEADBEEF'        -> key of DE AD BE EF 22
    7-byte card '04A1B2C3D4E5F6'  -> key of 88 04 A1 B2 9F   (only partly identifies it)
"""

from __future__ import annotations

CASCADE_TAG = 0x88


def _bcc(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


def legacy_key_for_uid(uid_hex: str) -> int | None:
    """The number the legacy system would have stored for a card with this UID."""
    try:
        uid = bytes.fromhex(uid_hex)
    except ValueError:
        return None
    if len(uid) == 4:
        first = uid
    elif len(uid) in (7, 10):
        first = bytes([CASCADE_TAG]) + uid[:3]
    else:
        return None
    return int.from_bytes(first + bytes([_bcc(first)]), "big")


def uid_for_legacy_key(key: int) -> str | None:
    """Recover a full UID from a legacy key, when the key holds all of it.

    Returns None for 7-byte cards (only partly stored) and for numbers that
    aren't valid 5-byte keys; those are matched by key on their first scan.
    """
    if not 0 < key < 1 << 40:
        return None
    data = key.to_bytes(5, "big")
    if _bcc(data[:4]) != data[4] or data[0] == CASCADE_TAG:
        return None
    return data[:4].hex().upper()


def placeholder_uid(key: int) -> str:
    """UID stored for an imported card until it is next scanned."""
    return f"LEGACY-{key}"
