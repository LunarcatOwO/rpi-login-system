"""Shows kiosk screens on a 16x2 LCD (the legacy system's display).

Line 1 is the screen title, line 2 is what's being typed or the first detail
line. Used on its own in headless mode, or alongside the touchscreen.
"""

from __future__ import annotations

import threading
import time

from nfc_login.kiosk.controller import Screen, local_ip

SHOW_IP_SECONDS = 60


class LcdOutput:
    def __init__(self, lcd, controller):
        self.lcd = lcd
        self.controller = controller
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._started = time.monotonic()

    def idle(self) -> None:
        # Like the legacy kiosk, show the Pi's address for a while after boot
        # so people can find the web page.
        recent = time.monotonic() - self._started < SHOW_IP_SECONDS
        self.lcd.show("Tap your card", local_ip() if recent else "or type your ID")

    def show(self, screen: Screen) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            if screen.title == self.controller.idle_screen().title:
                self.idle()
                return
            if screen.entry is not None:
                second = f">{screen.entry}"
            else:
                second = next((line for line in screen.lines if line.strip()), "")
            self.lcd.show(screen.title, second)
            if screen.hold_seconds:
                self._timer = threading.Timer(screen.hold_seconds, self._revert)
                self._timer.daemon = True
                self._timer.start()

    def _revert(self) -> None:
        if self.controller.state == "idle":
            self.idle()
