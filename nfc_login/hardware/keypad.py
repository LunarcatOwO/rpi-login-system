# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""4x4 matrix keypad from the SunFounder Da Vinci Kit (lesson 2.1.5)."""

from __future__ import annotations

import threading
import time
from typing import Callable


class MatrixKeypad:
    """Scans the keypad the same way as the kit's example code.

    A key joins one row wire to one column wire. Drive one row HIGH at a time
    and see which column reads HIGH: that row and column give the key.
    """

    def __init__(self, rows: list[int], cols: list[int], keys: list[str]):
        if len(keys) != len(rows) * len(cols):
            raise ValueError("keys must have rows x cols entries")
        import RPi.GPIO as GPIO

        self._gpio = GPIO
        self.rows = rows
        self.cols = cols
        self.keys = keys
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(rows, GPIO.OUT, initial=GPIO.LOW)
        # The Pi's internal pull-downs hold idle columns LOW, so no resistors are needed.
        GPIO.setup(cols, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    def read(self) -> set[str]:
        """Keys held down right now."""
        gpio = self._gpio
        pressed = set()
        for i, row in enumerate(self.rows):
            gpio.output(row, gpio.HIGH)
            for j, col in enumerate(self.cols):
                if gpio.input(col) == 1:
                    # keys is laid out row by row, so row i, column j is at i * 4 + j.
                    pressed.add(self.keys[i * len(self.cols) + j])
            gpio.output(row, gpio.LOW)
        return pressed

    def cleanup(self) -> None:
        """Hand the pins back (only ours: the PN532 and buzzer keep theirs)."""
        self._gpio.cleanup(self.rows + self.cols)


class KeypadPoller(threading.Thread):
    """Background thread that calls ``on_key`` once per key press."""

    def __init__(self, keypad: MatrixKeypad, on_key: Callable[[str], None], interval: float = 0.05):
        super().__init__(daemon=True, name="keypad")
        self.keypad = keypad
        self.on_key = on_key
        self.interval = interval
        self._stopped = threading.Event()

    def run(self) -> None:
        held: set[str] = set()
        while not self._stopped.is_set():
            pressed = self.keypad.read()
            # Only keys that weren't down last time: holding a key reports it once.
            for key in sorted(pressed - held):
                self.on_key(key)
            held = pressed
            time.sleep(self.interval)

    def stop(self) -> None:
        self._stopped.set()
