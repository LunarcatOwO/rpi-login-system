"""User IDs: a section letter plus a two-digit number, e.g. A07 or E12."""

from __future__ import annotations

DIGITS = 2
MAX_NUMBER = 10 ** DIGITS - 1

# Holding "section" for people imported from the legacy system. Their old cards
# carry no group, so they pick one on their first scan (see kiosk/controller.py).
UNSORTED = "U"
UNSORTED_NAME = "No group yet"


def format_code(section: str, number: int) -> str:
    return f"{section}{number:0{DIGITS}d}"


def parse_code(text: str) -> tuple[str, int]:
    """'a7', 'A07' -> ('A', 7). Raises ValueError for anything else."""
    text = text.strip().upper()
    if len(text) < 2 or not text[0].isalpha() or not text[1:].isdigit():
        raise ValueError(f"{text!r} isn't a user ID (a letter then a number, like A07)")
    number = int(text[1:])
    if not 1 <= number <= MAX_NUMBER:
        raise ValueError(f"user numbers go from 1 to {MAX_NUMBER}")
    return text[0], number


def with_code(row: dict | None) -> dict | None:
    """Add a 'code' key to a row that has section and number columns."""
    if row is not None and "section" in row and "number" in row:
        row["code"] = format_code(row["section"], row["number"])
    return row
