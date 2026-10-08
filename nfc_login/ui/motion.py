# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""The loading spinner's motion for the Tk window (kiosk.html does the same in CSS)."""

# No tkinter imports here, so the tests can run it anywhere.

from __future__ import annotations

# The loading spinner: five dots chasing round a circle, like Windows'. Each dot
# follows these keyframes (fraction of the cycle, angle in degrees, easing to the
# next one), a little behind the one before; it's hidden from 76% of its cycle.
SPIN_MS = 5_500
SPIN_DELAY_MS = 240
SPIN_KEYS = [(0, 225, "out"), (0.07, 345, "linear"), (0.30, 455, "inout"),
             (0.39, 690, "linear"), (0.70, 815, "out"), (0.75, 945, "out"), (0.76, 945, "")]


def _ease(kind: str, x: float) -> float:
    """Map progress x (0-1) to eased progress, for smooth starts and stops."""
    if kind == "out":
        return 1 - (1 - x) ** 3       # fast, then slowing down
    if kind == "inout":
        return x * x * (3 - 2 * x)    # slow, fast, slow
    return x                          # linear: constant speed


def spinner_angles(ms: float) -> list[float | None]:
    """Where each spinner dot is `ms` after it started: degrees clockwise from the
    top, or None while that dot is hidden."""
    angles: list[float | None] = []
    for n in range(5):
        t = ms - n * SPIN_DELAY_MS        # each dot starts a little later
        if t < 0:
            angles.append(None)       # not started yet
            continue
        f = (t % SPIN_MS) / SPIN_MS       # how far through its cycle, 0-1
        angle = None
        # Find the two keyframes f sits between and blend their angles.
        for (f0, a0, kind), (f1, a1, _) in zip(SPIN_KEYS, SPIN_KEYS[1:]):
            if f0 <= f < f1 and kind:
                angle = (a0 + (a1 - a0) * _ease(kind, (f - f0) / (f1 - f0))) % 360
                break
        angles.append(angle)
    return angles
