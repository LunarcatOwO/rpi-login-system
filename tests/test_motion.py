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

from nfc_login.ui.motion import SPIN_DELAY_MS, SPIN_MS, spinner_angles


def test_dots_start_one_after_another_at_the_same_place():
    assert spinner_angles(0) == [225, None, None, None, None]
    angles = spinner_angles(4 * SPIN_DELAY_MS)
    assert angles[4] == 225 and all(a is not None for a in angles)
    assert angles[0] != 225                                   # the first has moved on


def test_a_dot_goes_round_twice_then_hides_until_its_next_turn():
    assert spinner_angles(0.07 * SPIN_MS)[0] == pytest.approx(345)
    assert spinner_angles(0.75 * SPIN_MS)[0] == pytest.approx(945 % 360)
    assert spinner_angles(0.8 * SPIN_MS)[0] is None           # resting
    assert spinner_angles(SPIN_MS)[0] == 225                  # and round again


def test_angles_only_move_forward_while_showing():
    last, seen = None, 0
    for ms in range(0, int(0.75 * SPIN_MS), 20):
        a = spinner_angles(ms)[0]
        unwrapped = a if last is None else last + (a - last % 360) % 360
        assert last is None or unwrapped >= last - 1e-9
        last, seen = unwrapped, seen + 1
    assert last - 225 == pytest.approx(720, abs=5)            # two laps
