# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Buzzer on GPIO 12 (pin 32): a different rhythm for each event."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

log = logging.getLogger(__name__)

# Each pattern is milliseconds on, off, on, off...: [70, 50, 160] is a short
# beep, a gap, then a long beep. scripts/buzzer_samples.py turns them into WAVs.
PATTERNS: dict[str, list[int]] = {
    "sign_in": [70, 50, 70, 50, 180],          # da-da-DAA, rising
    "sign_out": [180, 50, 70, 50, 70],         # DAA-da-da, falling
    "success": [70, 50, 180],                  # da-DAA: card enrolled, hours saved
    "ignored": [60],                           # tapped again too soon
    # dit-dit-dit, dit-dit-dit: old card, pick your team
    "attention": [50, 50, 50, 50, 50, 250, 50, 50, 50, 50, 50],
    "admin": [40, 40, 40, 40, 40, 40, 40],     # four quick ticks: admin menu
    "warning": [150, 120, 150],                # two even beeps
    "error": [400, 100, 400],                  # two long buzzes: unknown card, wrong PIN
    "key": [25],                               # keypad click
}


class Buzzer:
    """Plays patterns through ``output(on: bool)`` on a worker thread."""

    def __init__(self, output: Callable[[bool], None], patterns: dict | None = None,
                 key_clicks: bool = True):
        self.output = output
        self.patterns = {**PATTERNS, **(patterns or {})}  # config.toml overrides win
        self.key_clicks = key_clicks
        self._queue: queue.Queue[str] = queue.Queue()
        self._interrupt = threading.Event()
        # Patterns play on their own thread so the screen never waits for a beep.
        threading.Thread(target=self._run, daemon=True, name="buzzer").start()

    def play(self, name: str | None) -> None:
        """Start a pattern by name, cutting off whatever is still playing."""
        if not name or name not in self.patterns:
            return
        if name == "key" and not self.key_clicks:
            return
        self._interrupt.set()          # stop the current pattern early
        self._queue.put(name)

    def _run(self) -> None:
        while True:
            name = self._queue.get()
            if not self._queue.empty():
                continue               # a newer pattern is already waiting
            self._interrupt.clear()
            try:
                for i, ms in enumerate(self.patterns[name]):
                    self.output(i % 2 == 0)              # even steps on, odd steps off
                    if self._interrupt.wait(ms / 1000):  # sleeps, but wakes on play()
                        break
            except Exception:
                log.exception("buzzer failed")
            finally:
                try:
                    self.output(False)  # never leave it buzzing
                except Exception:
                    pass


class GpioBuzzerOutput:
    """Drives the buzzer pin. Active buzzers just need the pin on; passive
    ones need a tone, so they get PWM at ``frequency``."""

    def __init__(self, pin: int, kind: str = "active", frequency: int = 2700,
                 active_low: bool = False):
        import RPi.GPIO as GPIO

        self._gpio = GPIO
        self.pin = pin
        self.active_low = active_low
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH if active_low else GPIO.LOW)  # start silent
        self._pwm = None
        if kind == "passive":
            self._pwm = GPIO.PWM(pin, frequency)
            self._pwm.start(0)
        elif kind != "active":
            raise ValueError(f"buzzer type must be 'active' or 'passive', not {kind!r}")

    def __call__(self, on: bool) -> None:
        if self._pwm is not None:
            self._pwm.ChangeDutyCycle(50 if on else 0)  # 50% duty = square wave tone
        else:
            self._gpio.output(self.pin, on != self.active_low)  # flips the level if active_low
