"""What gets written onto a user's card after every scan.

The card holds, in order:
  1. a URI record pointing at the GitHub Pages site (so a phone tap opens it),
  2. a Text record with the user's ID, name, season time, leaderboard rank and
     last sign-in / sign-out.

This is a convenience copy for people to read. The kiosk never reads it back:
users are identified by the tag's factory UID and hours come from MariaDB.

If a tag is too small for everything (an NTAG213 holds 137 bytes of NDEF),
progressively shorter versions are tried.
"""

from __future__ import annotations

from urllib.parse import urlencode

from nfc_login.services.attendance import UserStats
from nfc_login.services.timefmt import format_timestamp
from nfc_login.tags import ndef


class TagTooSmall(Exception):
    pass


def site_link(site_url: str, user_id: int) -> str:
    separator = "&" if "?" in site_url else "?"
    return f"{site_url}{separator}{urlencode({'id': user_id})}"


def full_text(stats: UserStats) -> str:
    hours, minutes = stats.hours_minutes
    rank = f"#{stats.rank} of {stats.ranked_users}" if stats.rank else "-"
    return "\n".join([
        f"ID: {stats.user_id}",
        f"User: {stats.username}",
        f"Season: {stats.season_name}",
        f"Time: {hours}h {minutes}m",
        f"Rank: {rank}",
        f"Last in: {format_timestamp(stats.last_sign_in)}",
        f"Last out: {format_timestamp(stats.last_sign_out)}",
    ])


def compact_text(stats: UserStats) -> str:
    hours, minutes = stats.hours_minutes
    last_in = stats.last_sign_in.strftime("%m-%d %H:%M") if stats.last_sign_in else "-"
    last_out = stats.last_sign_out.strftime("%m-%d %H:%M") if stats.last_sign_out else "-"
    return (
        f"{stats.user_id}|{stats.username}|{hours}h{minutes}m|#{stats.rank or '-'}"
        f"|in {last_in}|out {last_out}"
    )


def build_message(stats: UserStats, site_url: str, capacity: int | None = None) -> bytes:
    """Return the largest NDEF message variant that fits ``capacity`` bytes.

    ``capacity`` is the tag's NDEF data area (from its capability container);
    the TLV wrapper overhead is accounted for here.
    """
    link = ndef.uri_record(site_link(site_url, stats.user_id))
    variants = [
        [link, ndef.text_record(full_text(stats))],
        [link, ndef.text_record(compact_text(stats))],
        [ndef.text_record(compact_text(stats))],
        [link],
    ]
    for records in variants:
        message = ndef.encode_message(records)
        if capacity is None or len(ndef.wrap_tlv(message)) <= capacity:
            return message
    raise TagTooSmall(f"tag only holds {capacity} bytes")
