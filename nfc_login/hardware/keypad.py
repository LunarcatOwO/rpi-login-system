"""4x4 matrix keypad from the SunFounder Da Vinci Kit (lesson 2.1.5).

Same scanning approach as the kit's Python example: drive one row HIGH at a
time and read which column goes HIGH (columns are pulled down). Added here:
edge detection so holding a key reports it once.
"""

from __future__ import annotations

import threading
import time
from typing import Callable


class MatrixKeypad:
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
        GPIO.setup(cols, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    def read(self) -> set[str]:
        """Keys held down right now."""
        gpio = self._gpio
        pressed = set()
        for i, row in enumerate(self.rows):
            gpio.output(row, gpio.HIGH)
            for j, col in enumerate(self.cols):
                if gpio.input(col) == 1:
                    pressed.add(self.keys[i * len(self.cols) + j])
            gpio.output(row, gpio.LOW)
        return pressed

    def cleanup(self) -> None:
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
            for key in sorted(pressed - held):
                self.on_key(key)
            held = pressed
            time.sleep(self.interval)

    def stop(self) -> None:
        self._stopped.set()
