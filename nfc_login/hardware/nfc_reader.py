"""Elechouse PN532 NFC Module V3 over I2C.

Wiring and DIP switch settings are in docs/hardware.md. The module is used in
I2C mode because the Da Vinci Kit keypad already uses GPIO10 (SPI MOSI).
"""

from __future__ import annotations

from nfc_login.tags import ndef

FIRST_DATA_PAGE = 4
CC_PAGE = 3
PAGE_SIZE = 4


class TagWriteError(Exception):
    pass


def uid_to_hex(uid: bytes) -> str:
    return bytes(uid).hex().upper()


class PN532Reader:
    """Reads tag UIDs and writes NDEF to NTAG21x tags."""

    def __init__(self, poll_timeout: float = 0.5):
        # Imported here so the rest of the app runs on machines without Blinka.
        import board
        import busio
        from adafruit_pn532.i2c import PN532_I2C

        i2c = busio.I2C(board.SCL, board.SDA)
        self._pn532 = PN532_I2C(i2c, debug=False)
        self._pn532.SAM_configuration()
        self.poll_timeout = poll_timeout

    def firmware_version(self) -> str:
        ic, ver, rev, _support = self._pn532.firmware_version
        return f"PN5{ic:02x} firmware {ver}.{rev}"

    def read_uid(self) -> str | None:
        uid = self._pn532.read_passive_target(timeout=self.poll_timeout)
        return uid_to_hex(uid) if uid else None

    def _read_page(self, page: int) -> bytes | None:
        try:
            data = self._pn532.ntag2xx_read_block(page)
        except (TypeError, RuntimeError):
            return None
        return bytes(data) if data else None

    def ndef_capacity(self) -> int | None:
        """NDEF data area size in bytes, or None if the tag isn't NDEF-formatted Type 2.

        MIFARE Classic cards (4-byte UID) fail this check, so they still work
        for signing in but are not written to.
        """
        cc = self._read_page(CC_PAGE)
        if not cc or cc[0] != 0xE1:
            return None
        if cc[3] & 0x0F:  # write access bits: 0 means writable
            return None
        return cc[2] * 8

    def write_ndef(self, message: bytes) -> None:
        capacity = self.ndef_capacity()
        if capacity is None:
            raise TagWriteError("tag is not a writable NTAG (Type 2) tag")
        data = ndef.wrap_tlv(message)
        if len(data) > capacity:
            raise TagWriteError(f"message is {len(data)} bytes, tag holds {capacity}")
        data += b"\x00" * (-len(data) % PAGE_SIZE)
        for offset in range(0, len(data), PAGE_SIZE):
            page = FIRST_DATA_PAGE + offset // PAGE_SIZE
            if not self._pn532.ntag2xx_write_block(page, data[offset:offset + PAGE_SIZE]):
                raise TagWriteError(f"write failed at page {page} (card moved?)")
