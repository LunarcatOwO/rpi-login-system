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

# The loading spinner: four dots in a square that fold into a diagonal line and
# spring back out, first along one diagonal, then the other. Positions are in
# units of half the square's side, (0, 0) is the middle and y grows downwards.
SPIN_MS = 1_300
SQUARE = [(-1, -1), (1, -1), (-1, 1), (1, 1)]          # top left, top right, bottom left, bottom right
SLASH = [(.5, -.5), (1.5, -1.5), (-1.5, 1.5), (-.5, .5)]        # folded into a / line
SLASH_OUT = [(.4, -1), (1.1, -1), (-1.1, 1), (-.4, 1)]          # just after, springing back
BACKSLASH = [(-1.5, -1.5), (.5, .5), (-.5, -.5), (1.5, 1.5)]    # folded into a \ line
BACKSLASH_OUT = [(-1.1, -1), (-.4, -1), (.4, 1), (1.1, 1)]

# Easing curves, the same cubic-beziers as the CSS.
SNAP = (.2, .9, .3, 1)       # leaving the line: fast, then slowing
SETTLE = (.3, .6, .3, 1)     # easing back into the square
FOLD = (.6, 0, .9, .5)       # folding in: slow start, speeding up

# (fraction of the cycle, positions, easing to the next keyframe), like kiosk.html.
SPIN_KEYS = [(0, SLASH, SNAP), (.03, SLASH, SNAP), (.09, SLASH_OUT, SETTLE),
             (.30, SQUARE, FOLD), (.42, SQUARE, FOLD), (.50, BACKSLASH, SNAP),
             (.53, BACKSLASH, SNAP), (.59, BACKSLASH_OUT, SETTLE), (.80, SQUARE, FOLD),
             (.92, SQUARE, FOLD), (1, SLASH, None)]


def _bezier(curve: tuple, x: float) -> float:
    """CSS cubic-bezier(x1, y1, x2, y2): eased progress for progress x (0-1)."""
    x1, y1, x2, y2 = curve
    point = lambda t, a, b: 3 * a * t * (1 - t) ** 2 + 3 * b * t * t * (1 - t) + t ** 3
    lo, hi = 0.0, 1.0
    for _ in range(30):                  # find the t whose x matches, by halving
        t = (lo + hi) / 2
        if point(t, x1, x2) < x:
            lo = t
        else:
            hi = t
    return point((lo + hi) / 2, y1, y2)


def spinner_dots(ms: float) -> list[tuple[float, float]]:
    """Where the four spinner dots are `ms` after it started (see SQUARE for the units)."""
    f = (ms % SPIN_MS) / SPIN_MS             # how far through the cycle, 0-1
    # Find the two keyframes f sits between and blend their positions.
    for (f0, a, curve), (f1, b, _) in zip(SPIN_KEYS, SPIN_KEYS[1:]):
        if f0 <= f < f1:
            e = _bezier(curve, (f - f0) / (f1 - f0))
            return [(ax + (bx - ax) * e, ay + (by - ay) * e) for (ax, ay), (bx, by) in zip(a, b)]
    return list(SLASH)
