"""Touch versions of the keypad, shared by the kiosk windows.

A screen line made only of "K  label" parts (K one keypad key, two spaces,
then the label; parts separated by three or more spaces) is drawn as a row of
buttons that press those keys, e.g. "#  next page      *  back". kiosk.html
has the same parser in JavaScript (keyParts); tests/test_kiosk_window.py
checks that the two agree.
"""

from __future__ import annotations

import re

_PART = re.compile(r"([0-9A-D*#])  (\S.*)")
_GAP = re.compile(r" {3,}")

# The 4x4 keypad as drawn on screen for keyboard="keypad", with the captions
# that say what # and * do.
KEYPAD = ["123A", "456B", "789C", "*0#D"]
KEYPAD_CAPTIONS = {"#": "Enter", "*": "Delete / Back"}


def key_parts(line: str) -> list[tuple[str, str]] | None:
    """[(key, label), ...] if `line` is only "K  label" parts, else None (plain text)."""
    parts = []
    for chunk in _GAP.split(line.strip()):
        match = _PART.fullmatch(chunk)
        if not match:
            return None
        parts.append((match[1], match[2].rstrip()))
    return parts or None


MENU_COLUMNS_FROM = 5   # a run of this many one-key lines is drawn in two columns


def blocks(lines: list[str]) -> list[tuple[str, object]]:
    """A screen's lines grouped for drawing, in order:

    ("text", line)          plain text ("" is a little gap)
    ("row", [(k, l), ...])  a line of several keys: one row of buttons
    ("menu", [(k, l), ...]) a run of one-key lines: a column of buttons, or two
                            columns (filled downwards) from MENU_COLUMNS_FROM on
    """
    out: list[tuple[str, object]] = []
    for line in lines:
        parts = key_parts(line)
        if parts is None:
            out.append(("text", line))
        elif len(parts) > 1:
            out.append(("row", parts))
        elif out and out[-1][0] == "menu":
            out[-1][1].append(parts[0])
        else:
            out.append(("menu", parts))
    return out


def menu_columns(items: list) -> list[list]:
    """A menu's buttons split into its columns, filled downwards."""
    if len(items) < MENU_COLUMNS_FROM:
        return [items]
    half = -(-len(items) // 2)
    return [items[:half], items[half:]]
