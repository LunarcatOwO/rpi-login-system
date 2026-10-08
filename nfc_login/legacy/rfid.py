# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Card numbers from the legacy system, and how they map to UIDs."""

# The old kiosk's mfrc522 library (SimpleMFRC522) stored each card as one number
# made from 5 bytes: the 4 UID bytes plus a check byte (BCC = XOR of the four).
#   4-byte card DEADBEEF        -> DE AD BE EF 22
#   7-byte card 04A1B2C3D4E5F6  -> 88 04 A1 B2 9F
# A 7-byte card only gave the RC522 its first 3 bytes, after the 0x88 "cascade
# tag" that means "more UID follows", so that number only partly identifies it.

from __future__ import annotations

CASCADE_TAG = 0x88  # first byte of a 7- or 10-byte UID's first part


def _bcc(data: bytes) -> int:
    """XOR of all the bytes: the check byte cards send after their UID."""
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
    """Recover a full UID from a legacy key, when the key holds all of it."""
    if not 0 < key < 1 << 40:  # must fit in 5 bytes
        return None
    data = key.to_bytes(5, "big")
    # A bad check byte, or a 7-byte card (only partly stored): no full UID.
    # Those are matched by key on their first scan instead (repository.find_tag).
    if _bcc(data[:4]) != data[4] or data[0] == CASCADE_TAG:
        return None
    return data[:4].hex().upper()


def placeholder_uid(key: int) -> str:
    """UID stored for an imported card until it is next scanned."""
    return f"LEGACY-{key}"
