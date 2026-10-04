"""The loading spinner's motion, shared by the Tk window (kiosk.html does the same in CSS).

Kept free of tkinter so it can be tested anywhere.
"""

from __future__ import annotations

# The loading spinner: five dots chasing round a circle, like Windows'. Each dot
# follows these keyframes (fraction of the cycle, angle in degrees, easing to the
# next one), a little behind the one before; it's hidden from 76% of its cycle.
SPIN_MS = 5_500
SPIN_DELAY_MS = 240
SPIN_KEYS = [(0, 225, "out"), (0.07, 345, "linear"), (0.30, 455, "inout"),
             (0.39, 690, "linear"), (0.70, 815, "out"), (0.75, 945, "out"), (0.76, 945, "")]


def _ease(kind: str, x: float) -> float:
    if kind == "out":
        return 1 - (1 - x) ** 3
    if kind == "inout":
        return x * x * (3 - 2 * x)
    return x


def spinner_angles(ms: float) -> list[float | None]:
    """Where each spinner dot is `ms` after it started: degrees clockwise from the
    top, or None while that dot is hidden."""
    angles: list[float | None] = []
    for n in range(5):
        t = ms - n * SPIN_DELAY_MS
        if t < 0:
            angles.append(None)       # not started yet
            continue
        f = (t % SPIN_MS) / SPIN_MS
        angle = None
        for (f0, a0, kind), (f1, a1, _) in zip(SPIN_KEYS, SPIN_KEYS[1:]):
            if f0 <= f < f1 and kind:
                angle = (a0 + (a1 - a0) * _ease(kind, (f - f0) / (f1 - f0))) % 360
                break
        angles.append(angle)
    return angles
