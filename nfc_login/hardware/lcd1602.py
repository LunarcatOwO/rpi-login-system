"""16x2 character LCD on an I2C backpack (PCF8574), as used by the legacy system.

The legacy kiosk drove it at address 0x27 on I2C bus 1. Standard HD44780
4-bit protocol through the backpack; needs `smbus2`.
"""

from __future__ import annotations

import threading
import time

LCD_WIDTH = 16
LINE_ADDR = (0x80, 0xC0)
BACKLIGHT = 0x08
ENABLE = 0x04
REGISTER_SELECT = 0x01

# Characters the LCD can't show, mapped to ones it can.
REPLACEMENTS = {"•": "*", "·": "-", "—": "-", "→": ">", "←": "<", "…": "..."}


def to_lcd_text(text: str) -> str:
    text = "".join(REPLACEMENTS.get(c, c) for c in text)
    text = text.encode("ascii", "replace").decode()
    return text[:LCD_WIDTH].ljust(LCD_WIDTH)


class Lcd1602:
    def __init__(self, bus: int = 1, address: int = 0x27):
        from smbus2 import SMBus

        self._bus = SMBus(bus)
        self._addr = address
        self._lock = threading.Lock()
        for nibble in (0x33, 0x32):          # initialise into 4-bit mode
            self._command(nibble)
        self._command(0x28)                  # 2 lines, 5x8 font
        self._command(0x0C)                  # display on, cursor off
        self._command(0x06)                  # move cursor right
        self.clear()

    def _strobe(self, data: int) -> None:
        self._bus.write_byte(self._addr, data | ENABLE | BACKLIGHT)
        time.sleep(0.0005)
        self._bus.write_byte(self._addr, (data & ~ENABLE) | BACKLIGHT)
        time.sleep(0.0001)

    def _send(self, value: int, mode: int) -> None:
        for nibble in (value & 0xF0, (value << 4) & 0xF0):
            self._bus.write_byte(self._addr, nibble | mode | BACKLIGHT)
            self._strobe(nibble | mode)

    def _command(self, value: int) -> None:
        self._send(value, 0)

    def clear(self) -> None:
        with self._lock:
            self._command(0x01)
            time.sleep(0.002)

    def show(self, line1: str, line2: str = "") -> None:
        with self._lock:
            for addr, text in zip(LINE_ADDR, (line1, line2)):
                self._command(addr)
                for char in to_lcd_text(text):
                    self._send(ord(char), REGISTER_SELECT)
