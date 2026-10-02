"""Kiosk without the touchscreen: card reader + keypad + 16x2 LCD + web page.

This is how the legacy system ran. Select it with ``[ui] mode = "headless"``.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

from nfc_login.kiosk.controller import KioskController, Screen

log = logging.getLogger(__name__)


class HeadlessKiosk:
    def __init__(self, controller: KioskController, outputs: list, buzzer=None):
        self.controller = controller
        self.outputs = outputs          # objects with show(Screen)
        self.buzzer = buzzer
        self._keys: queue.Queue = queue.Queue()

    def publish(self, screen: Screen) -> None:
        if self.buzzer:
            self.buzzer.play(screen.buzz)
        for output in self.outputs:
            try:
                output.show(screen)
            except Exception:
                log.exception("display update failed")

    def press(self, key: str) -> None:
        if self.buzzer:
            self.buzzer.play("key")
        self._keys.put(key)

    def _key_loop(self) -> None:
        while True:
            key = self._keys.get()
            try:
                screen = self.controller.handle_key(key)
            except Exception as exc:
                log.exception("key %s failed", key)
                screen = Screen("Error", [str(exc)], "error", hold_seconds=6)
            if screen:
                self.publish(screen)

    def run_forever(self) -> None:
        threading.Thread(target=self._key_loop, daemon=True, name="keys").start()
        self.publish(self.controller.idle_screen())
        while True:
            screen = self.controller.check_timeout()
            if screen:
                self.publish(screen)
            time.sleep(1)
