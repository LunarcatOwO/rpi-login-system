# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Elechouse PN532 NFC Module V3, over SPI (the default), I2C or UART (HSU)."""

# SPI wiring: SCK pin 23, MISO 21, MOSI 19, SS 29 (GPIO 5), VCC 3.3 V, GND.
# DIP switches: SPI = 1 OFF, 2 ON (I2C = 1 ON, 2 OFF; UART = both OFF).
#
# Two kinds of card are written to:
#   NTAG21x (7-byte UID): NDEF from page 4, which phones can read.
#   MIFARE Classic 1K (4-byte UID, the legacy system's cards): the same bytes in
#   the data blocks of sectors 1-15, unlocked with the factory key. Phones can't
#   read these, but the kiosk can.

from __future__ import annotations

import time

from nfc_login.tags import ndef

# NTAG memory is 4-byte "pages". Pages 0-2 hold the UID and lock bits, page 3
# the capability container (CC: size, write access), user data starts at 4.
FIRST_DATA_PAGE = 4
CC_PAGE = 3
PAGE_SIZE = 4

# MIFARE Classic 1K: 16 sectors x 4 blocks of 16 bytes. The last block of each
# sector (the "trailer") holds its keys, so only 3 blocks per sector hold data.

CLASSIC_KEY = b"\xff" * 6          # factory default key A
CLASSIC_AUTH_A = 0x60
CLASSIC_SECTORS = range(1, 16)     # sector 0 holds the manufacturer block
CLASSIC_BLOCK_SIZE = 16
CLASSIC_CAPACITY = len(CLASSIC_SECTORS) * 3 * CLASSIC_BLOCK_SIZE  # 720 bytes

TRIES = 4                          # attempts per block, or passes over missing blocks
BUDGET_SECONDS = 4.0               # give up on one card after this long
RESELECT_TIMEOUT = 0.15            # seconds to wait for the card to answer again


class TagWriteError(Exception):
    pass


class TagReadError(Exception):
    pass


def uid_to_hex(uid: bytes) -> str:
    """b"\\x04\\xa1..." -> "04A1...", how UIDs are stored in the database."""
    return bytes(uid).hex().upper()


def classic_data_blocks():
    """Block numbers that hold data on a MIFARE Classic 1K (sector trailers skipped)."""
    for sector in CLASSIC_SECTORS:
        for offset in range(3):          # blocks 0-2 of the sector; 3 is the trailer
            yield sector * 4 + offset


def open_pn532(nfc: dict):
    """Connect to the PN532 the way ``[hardware.nfc]`` says it's wired."""
    # Imported here so the rest of the app runs on machines without Blinka.
    import board
    import busio

    interface = nfc.get("interface", "spi")
    if interface == "spi":
        import digitalio
        from adafruit_bus_device.spi_device import SPIDevice
        from adafruit_pn532.adafruit_pn532 import PN532
        from adafruit_pn532.spi import _SPI_STATREAD, PN532_SPI, reverse_bit

        class FastPN532SPI(PN532_SPI):
            # The library talks SPI at 100 kHz and checks whether the PN532 is
            # done every 10 ms. The PN532 handles up to 5 MHz and usually answers
            # in a few ms, so a faster clock and a 1 ms check cut the wait per
            # command (and a card write is ~50 commands).
            def __init__(self, spi, cs_pin, baudrate):
                self.debug = False
                self._spi = SPIDevice(spi, cs_pin, baudrate=baudrate)
                PN532.__init__(self, debug=False)

            def _wait_ready(self, timeout=1):
                # Ask the PN532 for its status byte until it says 0x01 (ready).
                # It sends bits in the opposite order to the Pi, hence reverse_bit.
                cmd = bytearray([reverse_bit(_SPI_STATREAD), 0x00])
                response = bytearray(2)
                deadline = time.monotonic() + timeout
                with self._spi as spi:
                    while time.monotonic() < deadline:
                        spi.write_readinto(cmd, response)
                        if reverse_bit(response[1]) == 0x01:
                            return True
                        time.sleep(0.001)
                return False

        spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
        # Chip select on its own GPIO (5 by default), not the Pi's CE0 pin.
        cs = digitalio.DigitalInOut(getattr(board, f"D{nfc.get('spi_cs_pin', 5)}"))
        pn532 = FastPN532SPI(spi, cs, nfc.get("spi_baudrate", 1_000_000))
    elif interface == "i2c":
        from adafruit_pn532.i2c import PN532_I2C
        pn532 = PN532_I2C(busio.I2C(board.SCL, board.SDA), debug=False)
    elif interface == "uart":
        import serial
        from adafruit_pn532.uart import PN532_UART
        port = serial.Serial(nfc.get("uart_port", "/dev/serial0"), baudrate=115200, timeout=0.1)
        pn532 = PN532_UART(port, debug=False)
    else:
        raise ValueError(f"hardware.nfc.interface must be spi, i2c or uart, not {interface!r}")
    pn532.SAM_configuration()  # put the PN532 in normal card-reading mode
    return pn532


class PN532Reader:
    """Reads card UIDs and reads/writes NDEF on NTAG21x and MIFARE Classic cards.

    A read or write that fails part-way (the card wobbles, a wiring glitch) is
    retried: the card is selected again, its MIFARE sector unlocked again, and
    only the missing block redone, within ``budget`` seconds so the kiosk never
    hangs on one card.
    """

    def __init__(self, poll_timeout: float = 0.5, pn532=None, nfc: dict | None = None,
                 tries: int = TRIES, budget: float = BUDGET_SECONDS,
                 monotonic=time.monotonic):
        self._pn532 = pn532 if pn532 is not None else open_pn532(nfc or {})
        self.poll_timeout = poll_timeout
        self.tries = tries
        self.budget = budget
        self._monotonic = monotonic
        self._uid: bytes | None = None   # last card read, target of reads and writes
        self._deadline = 0.0
        self._firmware: str | None = None

    def firmware_version(self) -> str:
        # Asked once, at startup. Asking again later (System info on the admin
        # menu) would talk to the PN532 from the keypad thread while the card
        # thread is mid-poll, which garbles both and froze the kiosk.
        if self._firmware is None:
            ic, ver, rev, _support = self._pn532.firmware_version
            self._firmware = f"PN5{ic:02x} firmware {ver}.{rev}"
        return self._firmware

    def read_uid(self) -> str | None:
        """The UID of a card on the reader as hex, or None if there isn't one."""
        uid = self._pn532.read_passive_target(timeout=self.poll_timeout)
        if not uid:
            return None
        self._uid = bytes(uid)  # later reads/writes go to this card
        return uid_to_hex(uid)

    def is_classic(self) -> bool:
        """MIFARE Classic cards have 4-byte UIDs; NTAGs have 7."""
        return self._uid is not None and len(self._uid) == 4

    # ------------------------------------------------------------ retrying

    def _start(self) -> None:
        """Start the time budget for one card read or write."""
        self._deadline = self._monotonic() + self.budget

    def _call(self, fn, *args):
        """One PN532 command; a bus error or a missing reply counts as a failure."""
        try:
            return fn(*args)
        except (RuntimeError, OSError, TypeError):
            return None

    def _reselect(self) -> bool:
        """Wake the card again after a failed command. False if it's gone or swapped."""
        uid = self._call(self._pn532.read_passive_target, RESELECT_TIMEOUT)
        return bool(uid) and bytes(uid) == self._uid

    def _retry(self, fn, *args, sector: int | None = None):
        """Run a command up to ``tries`` times, selecting the card again (and
        unlocking its sector, on MIFARE Classic) between attempts."""
        for attempt in range(self.tries):
            if attempt:  # a retry: out of time, or the card is gone? stop
                if self._monotonic() > self._deadline or not self._reselect():
                    return None
                # A failed command locks a MIFARE sector again, so unlock it first.
                if sector is not None and not self._call(self._auth, sector):
                    continue
            result = self._call(fn, *args)
            if result:
                return result
        return None

    def _auth(self, sector: int) -> bool:
        # Unlock a MIFARE Classic sector by its first block, with key A.
        return self._pn532.mifare_classic_authenticate_block(
            self._uid, sector * 4, CLASSIC_AUTH_A, CLASSIC_KEY)

    def _unlock(self, sector: int) -> None:
        if not self._retry(self._auth, sector):
            raise TagWriteError(f"sector {sector} doesn't use the default key "
                                "(or the card was taken away)")

    # ------------------------------------------------------------ capacity

    def _read_page(self, page: int) -> bytes | None:
        data = self._retry(self._pn532.ntag2xx_read_block, page)
        return bytes(data[:PAGE_SIZE]) if data else None

    def ndef_capacity(self) -> int | None:
        """Bytes available for card info, or None if this card can't be written."""
        if self.is_classic():
            return CLASSIC_CAPACITY
        self._start()
        cc = self._read_page(CC_PAGE)
        if not cc or cc[0] != 0xE1:  # 0xE1 marks an NDEF-formatted tag
            return None
        if cc[3] & 0x0F:  # write access bits: 0 means writable
            return None
        return cc[2] * 8  # CC byte 2 is the data size in units of 8 bytes

    # ------------------------------------------------------------ writing

    def write_ndef(self, message: bytes) -> None:
        """Write an NDEF message to the card that was just read."""
        capacity = self.ndef_capacity()
        if capacity is None:
            raise TagWriteError("tag is not a writable NTAG or MIFARE Classic card")
        data = ndef.wrap_tlv(message)  # the message plus its TLV start/end markers
        if len(data) > capacity:
            raise TagWriteError(f"message is {len(data)} bytes, tag holds {capacity}")
        self._start()
        if self.is_classic():
            self._write_classic(data)
            return
        data += b"\x00" * (-len(data) % PAGE_SIZE)  # pad to whole pages
        for offset in range(0, len(data), PAGE_SIZE):
            page = FIRST_DATA_PAGE + offset // PAGE_SIZE
            if not self._retry(self._pn532.ntag2xx_write_block, page,
                               data[offset:offset + PAGE_SIZE]):
                raise TagWriteError(f"write failed at page {page} (card moved?)")

    def _write_classic(self, data: bytes) -> None:
        data += b"\x00" * (-len(data) % CLASSIC_BLOCK_SIZE)  # pad to whole blocks
        # Pair each 16-byte chunk with the next data block (trailers skipped).
        chunks = [data[i:i + CLASSIC_BLOCK_SIZE] for i in range(0, len(data), CLASSIC_BLOCK_SIZE)]
        blocks = list(zip(classic_data_blocks(), chunks))
        # Check every sector's key before writing anything, so a card with a
        # changed key part-way through isn't left half rewritten.
        for sector in sorted({block // 4 for block, _ in blocks}):
            self._unlock(sector)
        unlocked = None
        for block, chunk in blocks:
            sector = block // 4
            if sector != unlocked:  # moved into a new sector: unlock it
                self._unlock(sector)
                unlocked = sector
            if not self._retry(self._pn532.mifare_classic_write_block, block, chunk,
                               sector=sector):
                raise TagWriteError(f"write failed at block {block} (card moved?)")

    # ------------------------------------------------------------ reading

    def read_ndef(self) -> bytes:
        """The NDEF message on the card; TagReadError if there's none or it can't be read."""
        capacity = self.ndef_capacity()
        if capacity is None:
            raise TagReadError("not an NTAG or MIFARE Classic card")
        self._start()
        classic = self.is_classic()
        blocks = (list(classic_data_blocks()) if classic
                  else [FIRST_DATA_PAGE + n for n in range(capacity // PAGE_SIZE)])
        data = b""
        unlocked = None
        for block in blocks:
            if classic:
                sector = block // 4
                if sector != unlocked:
                    if not self._retry(self._auth, sector):
                        raise TagReadError(f"sector {sector} doesn't use the default key "
                                           "(or the card was taken away)")
                    unlocked = sector
                chunk = self._retry(self._pn532.mifare_classic_read_block, block,
                                    sector=sector)
                chunk = bytes(chunk[:CLASSIC_BLOCK_SIZE]) if chunk else None
            else:
                chunk = self._read_page(block)
            if chunk is None:
                raise TagReadError(f"couldn't read block {block} after {self.tries} tries; "
                                   "hold the card still")
            data += chunk
            # Stop as soon as the whole message is in, instead of reading every block.
            end = ndef.tlv_end(data)
            if end is not None and end <= len(data):
                break
        try:
            return ndef.unwrap_tlv(data)
        except ValueError:
            raise TagReadError("no card info on this card") from None
