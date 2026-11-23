# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""The loading spinner (nfc_login/ui/motion.py) and when screens ask for it."""

from __future__ import annotations

import pytest

from nfc_login.ui.motion import BACKSLASH, SLASH, SPIN_MS, SQUARE, spinner_dots


def test_starts_folded_into_a_slash_line():
    assert spinner_dots(0) == pytest.approx(SLASH)
    assert all(x == pytest.approx(-y) for x, y in spinner_dots(0))     # all on the / diagonal


def test_rests_as_a_square_then_folds_the_other_way():
    assert spinner_dots(0.35 * SPIN_MS) == pytest.approx(SQUARE)
    assert spinner_dots(0.50 * SPIN_MS) == pytest.approx(BACKSLASH)
    assert all(x == pytest.approx(y) for x, y in spinner_dots(0.5 * SPIN_MS))   # the \ diagonal
    assert spinner_dots(0.85 * SPIN_MS) == pytest.approx(SQUARE)
    assert spinner_dots(SPIN_MS) == pytest.approx(SLASH)               # and round again


def test_dots_move_smoothly_and_stay_inside_the_icon():
    last = spinner_dots(0)
    for ms in range(10, 2 * SPIN_MS, 10):
        dots = spinner_dots(ms)
        assert all(abs(x) <= 1.5 + 1e-9 and abs(y) <= 1.5 + 1e-9 for x, y in dots)
        # no jumps between frames 10 ms apart (leaving the line is the quickest part)
        assert max(abs(a - c) + abs(b - d) for (a, b), (c, d) in zip(dots, last)) < 1.5
        last = dots
