# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""What gets written onto a user's card after every scan."""

# The card holds a link to the GitHub Pages site (a phone tap opens it) and a
# text record with the person's ID, name, season time, rank and last in/out.
# It's only a copy for people to read: the kiosk never reads it back. People are
# identified by the card's factory UID, and hours come from MariaDB.

from __future__ import annotations

from urllib.parse import urlencode

from nfc_login.services.attendance import UserStats
from nfc_login.services.timefmt import format_timestamp
from nfc_login.tags import ndef


class TagTooSmall(Exception):
    """Not even the shortest version fits on the card."""


def site_link(site_url: str, code: str) -> str:
    """The site link with the person's ID added, e.g. ...?id=A007."""
    separator = "&" if "?" in site_url else "?"
    return f"{site_url}{separator}{urlencode({'id': code})}"


def full_text(stats: UserStats) -> str:
    """The long version, one item per line."""
    hours, minutes = stats.hours_minutes
    rank = f"#{stats.rank} of {stats.ranked_users}" if stats.rank else "-"
    return "\n".join([
        f"ID: {stats.code}",
        f"User: {stats.username}",
        f"Season: {stats.season_name}",
        f"Time: {hours}h {minutes}m",
        f"Rank: {rank}",
        f"Last in: {format_timestamp(stats.last_sign_in)}",
        f"Last out: {format_timestamp(stats.last_sign_out)}",
    ])


def compact_text(stats: UserStats) -> str:
    """The short version for small cards: A007|Taylor|12h30m|#3|in ...|out ..."""
    hours, minutes = stats.hours_minutes
    last_in = stats.last_sign_in.strftime("%m-%d %H:%M") if stats.last_sign_in else "-"
    last_out = stats.last_sign_out.strftime("%m-%d %H:%M") if stats.last_sign_out else "-"
    return (
        f"{stats.code}|{stats.username}|{hours}h{minutes}m|#{stats.rank or '-'}"
        f"|in {last_in}|out {last_out}"
    )


def build_message(stats: UserStats, site_url: str, capacity: int | None = None) -> bytes:
    """Return the largest NDEF message variant that fits ``capacity`` bytes."""
    link = ndef.uri_record(site_link(site_url, stats.code))
    # Longest first. An NTAG213 only holds 137 bytes, so it gets a shorter one.
    variants = [
        [link, ndef.text_record(full_text(stats))],
        [link, ndef.text_record(compact_text(stats))],
        [ndef.text_record(compact_text(stats))],
        [link],
    ]
    for records in variants:
        message = ndef.encode_message(records)
        # Measure with the TLV wrapper, since that's what goes on the card.
        if capacity is None or len(ndef.wrap_tlv(message)) <= capacity:
            return message
    raise TagTooSmall(f"tag only holds {capacity} bytes")
