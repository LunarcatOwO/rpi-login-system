"""User IDs: a team letter plus a three-digit number (A007), or for mentors
just the number (007)."""

from __future__ import annotations

DIGITS = 3
MAX_NUMBER = 10 ** DIGITS - 1

# Mentors' "section". Their IDs are number-only, typed on the keypad with no letter.
MENTORS = "M"

# Holding "section" for people imported from the legacy system. Their old cards
# carry no team, so an admin picks one on their first scan (see kiosk/controller.py).
UNSORTED = "U"
UNSORTED_NAME = "No team yet"


def format_code(section: str, number: int) -> str:
    prefix = "" if section == MENTORS else section
    return f"{prefix}{number:0{DIGITS}d}"


def parse_code(text: str) -> tuple[str, int]:
    """'a7', 'A007' -> ('A', 7); '12', '012' -> ('M', 12). Raises ValueError otherwise."""
    text = text.strip().upper()
    if text.isdigit():
        section, digits = MENTORS, text
    elif len(text) >= 2 and text[0].isalpha() and text[1:].isdigit():
        section, digits = text[0], text[1:]
    else:
        raise ValueError(f"{text!r} isn't a user ID (a team letter then a number like A007, "
                         "or a mentor number like 007)")
    number = int(digits)
    if len(digits) > DIGITS or not 1 <= number <= MAX_NUMBER:
        raise ValueError(f"user numbers go from 1 to {MAX_NUMBER}")
    return section, number


def code_length(typed: str) -> int:
    """How many characters a full ID has, judging by how it starts."""
    return DIGITS + (1 if typed[:1].isalpha() else 0)


def with_code(row: dict | None) -> dict | None:
    """Add a 'code' key to a row that has section and number columns."""
    if row is not None and "section" in row and "number" in row:
        row["code"] = format_code(row["section"], row["number"])
    return row
