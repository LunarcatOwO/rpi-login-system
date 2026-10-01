"""Background thread that polls the NFC reader and hands scans to the controller."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from nfc_login.kiosk.controller import KioskController, Screen

log = logging.getLogger(__name__)


class NfcWorker(threading.Thread):
    def __init__(self, reader, controller: KioskController, publish: Callable[[Screen], None]):
        super().__init__(daemon=True, name="nfc")
        self.reader = reader
        self.controller = controller
        self.publish = publish
        self._stopped = threading.Event()

    def run(self) -> None:
        present: str | None = None
        while not self._stopped.is_set():
            try:
                uid = self.reader.read_uid()
            except Exception:  # I2C hiccups shouldn't kill the kiosk
                log.exception("NFC read failed")
                time.sleep(1)
                continue
            if uid is None:
                present = None
                continue
            if uid == present:
                continue  # same card still sitting on the reader
            present = uid
            try:
                screen = self.controller.handle_card(uid)
            except Exception as exc:
                log.exception("scan of %s failed", uid)
                screen = Screen("Something went wrong", [str(exc)], "error", hold_seconds=6)
            self.publish(screen)

    def stop(self) -> None:
        self._stopped.set()
