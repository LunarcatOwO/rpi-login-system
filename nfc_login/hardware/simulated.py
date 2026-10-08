# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Stand-ins for the NFC reader so the kiosk runs on any computer.

In simulated mode the UI shows a small "tap card" box and maps the computer
keyboard to the keypad (see docs/usage.md).
"""

from __future__ import annotations

import threading

from nfc_login.hardware.nfc_reader import TagWriteError
from nfc_login.tags import ndef

NTAG215_CAPACITY = 496


class SimulatedReader:
    def __init__(self, capacity: int | None = NTAG215_CAPACITY):
        self.capacity = capacity
        self.memory: dict[str, bytes] = {}   # uid -> last NDEF TLV written
        self._present: str | None = None
        self._current: str | None = None     # last card read, target of writes
        self._lock = threading.Lock()
        self._tapped = threading.Event()

    def firmware_version(self) -> str:
        return "simulated reader"

    def tap(self, uid: str) -> None:
        """Pretend a card was placed on the reader (and removed after one read)."""
        with self._lock:
            self._present = uid.strip().upper()
        self._tapped.set()

    def read_uid(self) -> str | None:
        self._tapped.wait(timeout=0.5)
        with self._lock:
            uid, self._present = self._present, None
            if uid:
                self._current = uid
            self._tapped.clear()
        return uid

    def ndef_capacity(self) -> int | None:
        return self.capacity

    def write_ndef(self, message: bytes) -> None:
        data = ndef.wrap_tlv(message)
        if self.capacity is None or len(data) > self.capacity:
            raise TagWriteError("simulated tag too small")
        self.memory[self._current] = data
