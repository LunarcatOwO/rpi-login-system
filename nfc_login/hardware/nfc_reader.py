"""Elechouse PN532 NFC Module V3 over I2C.

Wiring and DIP switch settings are in docs/hardware.md. The module is used in
I2C mode because the Da Vinci Kit keypad already uses GPIO10 (SPI MOSI).

Two kinds of card are written to:

- NTAG21x stickers and cards (7-byte UID): NDEF from page 4, which phones read.
- MIFARE Classic 1K (4-byte UID, the legacy system's cards): the same NDEF
  bytes in the data blocks of sectors 1-15 using the factory key. Phones can't
  read these, but the kiosk and other readers can.
"""

from __future__ import annotations

from nfc_login.tags import ndef

FIRST_DATA_PAGE = 4
CC_PAGE = 3
PAGE_SIZE = 4

CLASSIC_KEY = b"\xff" * 6          # factory default key A
CLASSIC_AUTH_A = 0x60
CLASSIC_SECTORS = range(1, 16)     # sector 0 holds the manufacturer block
CLASSIC_BLOCK_SIZE = 16
CLASSIC_CAPACITY = len(CLASSIC_SECTORS) * 3 * CLASSIC_BLOCK_SIZE  # 720 bytes


class TagWriteError(Exception):
    pass


def uid_to_hex(uid: bytes) -> str:
    return bytes(uid).hex().upper()


def classic_data_blocks():
    """Block numbers that hold data on a MIFARE Classic 1K (sector trailers skipped)."""
    for sector in CLASSIC_SECTORS:
        for offset in range(3):
            yield sector * 4 + offset


class PN532Reader:
    """Reads card UIDs and writes NDEF to NTAG21x and MIFARE Classic cards."""

    def __init__(self, poll_timeout: float = 0.5, pn532=None):
        if pn532 is None:
            # Imported here so the rest of the app runs on machines without Blinka.
            import board
            import busio
            from adafruit_pn532.i2c import PN532_I2C

            i2c = busio.I2C(board.SCL, board.SDA)
            pn532 = PN532_I2C(i2c, debug=False)
            pn532.SAM_configuration()
        self._pn532 = pn532
        self.poll_timeout = poll_timeout
        self._uid: bytes | None = None   # last card read, target of writes

    def firmware_version(self) -> str:
        ic, ver, rev, _support = self._pn532.firmware_version
        return f"PN5{ic:02x} firmware {ver}.{rev}"

    def read_uid(self) -> str | None:
        uid = self._pn532.read_passive_target(timeout=self.poll_timeout)
        if not uid:
            return None
        self._uid = bytes(uid)
        return uid_to_hex(uid)

    def is_classic(self) -> bool:
        """MIFARE Classic cards have 4-byte UIDs; NTAGs have 7."""
        return self._uid is not None and len(self._uid) == 4

    def _read_page(self, page: int) -> bytes | None:
        try:
            data = self._pn532.ntag2xx_read_block(page)
        except (TypeError, RuntimeError):
            return None
        return bytes(data) if data else None

    def ndef_capacity(self) -> int | None:
        """Bytes available for card info, or None if this card can't be written."""
        if self.is_classic():
            return CLASSIC_CAPACITY
        cc = self._read_page(CC_PAGE)
        if not cc or cc[0] != 0xE1:
            return None
        if cc[3] & 0x0F:  # write access bits: 0 means writable
            return None
        return cc[2] * 8

    def write_ndef(self, message: bytes) -> None:
        capacity = self.ndef_capacity()
        if capacity is None:
            raise TagWriteError("tag is not a writable NTAG or MIFARE Classic card")
        data = ndef.wrap_tlv(message)
        if len(data) > capacity:
            raise TagWriteError(f"message is {len(data)} bytes, tag holds {capacity}")
        if self.is_classic():
            self._write_classic(data)
            return
        data += b"\x00" * (-len(data) % PAGE_SIZE)
        for offset in range(0, len(data), PAGE_SIZE):
            page = FIRST_DATA_PAGE + offset // PAGE_SIZE
            if not self._pn532.ntag2xx_write_block(page, data[offset:offset + PAGE_SIZE]):
                raise TagWriteError(f"write failed at page {page} (card moved?)")

    def _write_classic(self, data: bytes) -> None:
        data += b"\x00" * (-len(data) % CLASSIC_BLOCK_SIZE)
        chunks = [data[i:i + CLASSIC_BLOCK_SIZE] for i in range(0, len(data), CLASSIC_BLOCK_SIZE)]
        authed_sector = None
        for block, chunk in zip(classic_data_blocks(), chunks):
            sector = block // 4
            if sector != authed_sector:
                if not self._pn532.mifare_classic_authenticate_block(
                        self._uid, block, CLASSIC_AUTH_A, CLASSIC_KEY):
                    raise TagWriteError(f"sector {sector} doesn't use the default key")
                authed_sector = sector
            if not self._pn532.mifare_classic_write_block(block, chunk):
                raise TagWriteError(f"write failed at block {block} (card moved?)")
