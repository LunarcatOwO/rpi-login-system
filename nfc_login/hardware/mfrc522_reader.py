"""MFRC522 (RC522) RFID reader over SPI, as used by the legacy system.

Wiring is in docs/legacy.md. Uses the same `mfrc522` library as the legacy
kiosk. Limits compared with the PN532:

- Only the first anticollision level is read, so a 7-byte NTAG card shows up
  as 88 + its first 3 UID bytes (the legacy system had the same limit).
  Cards still match: see nfc_login.legacy.rfid and repository.find_tag.
- Nothing is written to cards (the stats/link are only written by a PN532).
"""

from __future__ import annotations

import time


class MFRC522Reader:
    def __init__(self, bus: int = 0, device: int = 0, rst_pin: int = 25):
        import RPi.GPIO as GPIO
        from mfrc522 import MFRC522

        # The library would otherwise pick BOARD numbering, which clashes with
        # the keypad's BCM numbering.
        if GPIO.getmode() is None:
            GPIO.setmode(GPIO.BCM)
        self._mf = MFRC522(bus=bus, device=device, pin_rst=rst_pin, pin_mode=GPIO.BCM)

    def firmware_version(self) -> str:
        return "MFRC522 (RC522)"

    def read_uid(self) -> str | None:
        mf = self._mf
        status, _tag_type = mf.MFRC522_Request(mf.PICC_REQIDL)
        if status != mf.MI_OK:
            time.sleep(0.2)  # same poll rate as the legacy script
            return None
        status, uid = mf.MFRC522_Anticoll()
        if status != mf.MI_OK or len(uid) < 4:
            return None
        return bytes(uid[:4]).hex().upper()

    def ndef_capacity(self) -> int | None:
        return None  # writing card info needs the PN532

    def write_ndef(self, message: bytes) -> None:
        from nfc_login.hardware.nfc_reader import TagWriteError
        raise TagWriteError("the RC522 reader doesn't write card info")
