# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Background thread that polls the NFC reader and hands scans to the controller."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from nfc_login.kiosk.controller import KioskController, Screen

log = logging.getLogger(__name__)


class NfcWorker(threading.Thread):
    """Asks the reader for a card over and over; each new card goes to the controller."""

    def __init__(self, reader, controller: KioskController, publish: Callable[[Screen], None]):
        super().__init__(daemon=True, name="nfc")
        self.reader = reader
        self.controller = controller
        self.publish = publish
        self._stopped = threading.Event()

    def run(self) -> None:
        present: str | None = None  # the card on the reader now, if any
        while not self._stopped.is_set():
            try:
                uid = self.reader.read_uid()  # waits up to poll_timeout for a card
            except Exception:  # I2C hiccups shouldn't kill the kiosk
                log.exception("NFC read failed")
                time.sleep(1)
                continue
            if uid is None:
                present = None  # card taken away: the next tap counts again
                continue
            if uid == present:
                continue  # same card still sitting on the reader
            present = uid
            try:
                screen = self.controller.handle_card(uid)
            except Exception as exc:
                log.exception("scan of %s failed", uid)
                screen = Screen("Something went wrong", [str(exc)], "error", hold_seconds=6)
            self.publish(screen)  # hand the result to the screen's thread

    def stop(self) -> None:
        self._stopped.set()
