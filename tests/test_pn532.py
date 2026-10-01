"""PN532Reader against a fake PN532, for NTAG and MIFARE Classic cards."""

from __future__ import annotations

import pytest

from nfc_login.hardware.nfc_reader import (
    CLASSIC_CAPACITY,
    CLASSIC_KEY,
    PN532Reader,
    TagWriteError,
)
from nfc_login.tags import ndef


class FakePN532:
    def __init__(self, uid: bytes, cc: bytes | None = None, key: bytes = CLASSIC_KEY,
                 changed_sectors: tuple[int, ...] = ()):
        self.uid = uid
        self.cc = cc
        self.key = key
        self.changed_sectors = changed_sectors
        self.pages: dict[int, bytes] = {}
        self.blocks: dict[int, bytes] = {}
        self.authed_sector = None

    def read_passive_target(self, timeout):
        return bytearray(self.uid)

    def ntag2xx_read_block(self, page):
        if self.cc is None:
            raise RuntimeError("not a Type 2 tag")
        return self.cc if page == 3 else self.pages.get(page, b"\x00" * 4)

    def ntag2xx_write_block(self, page, data):
        assert len(data) == 4
        self.pages[page] = bytes(data)
        return True

    def mifare_classic_authenticate_block(self, uid, block, key_number, key):
        assert bytes(uid) == self.uid and key_number == 0x60
        ok = bytes(key) == self.key and block // 4 not in self.changed_sectors
        self.authed_sector = block // 4 if ok else None
        return ok

    def mifare_classic_write_block(self, block, data):
        assert len(data) == 16
        assert block % 4 != 3, "never write a sector trailer"
        assert block >= 4, "never write sector 0"
        assert self.authed_sector == block // 4
        self.blocks[block] = bytes(data)
        return True


def classic_bytes(fake: FakePN532) -> bytes:
    return b"".join(fake.blocks[b] for b in sorted(fake.blocks))


def test_classic_card_from_the_legacy_system_is_written():
    fake = FakePN532(bytes.fromhex("22DD51C1"))      # the MIFARE Classic 1K from the legacy system
    reader = PN532Reader(pn532=fake)
    assert reader.read_uid() == "22DD51C1"
    assert reader.is_classic()
    assert reader.ndef_capacity() == CLASSIC_CAPACITY == 720
    message = ndef.encode_message([ndef.text_record("A07 Taylor " + "x" * 100)])
    reader.write_ndef(message)
    data = classic_bytes(fake)
    assert ndef.unwrap_tlv(data) == message
    assert min(fake.blocks) == 4 and 7 not in fake.blocks   # skipped the sector 1 trailer


def test_classic_card_with_changed_key_is_refused():
    fake = FakePN532(bytes.fromhex("22DD51C1"), key=b"\x00" * 6)
    reader = PN532Reader(pn532=fake)
    reader.read_uid()
    with pytest.raises(TagWriteError, match="default key"):
        reader.write_ndef(b"\xd1" + b"x" * 10)
    assert fake.blocks == {}


def test_classic_key_changed_part_way_writes_nothing():
    fake = FakePN532(bytes.fromhex("22DD51C1"), changed_sectors=(2,))
    reader = PN532Reader(pn532=fake)
    reader.read_uid()
    with pytest.raises(TagWriteError, match="sector 2"):
        reader.write_ndef(b"\xd1" + b"x" * 60)      # spans sectors 1 and 2
    assert fake.blocks == {}


def test_ntag_still_written_by_page():
    fake = FakePN532(bytes.fromhex("04634A2A772681"), cc=bytes([0xE1, 0x10, 0x3E, 0x00]))
    reader = PN532Reader(pn532=fake)
    reader.read_uid()
    assert not reader.is_classic()
    assert reader.ndef_capacity() == 496
    message = b"\xd1" + b"y" * 20
    reader.write_ndef(message)
    data = b"".join(fake.pages[p] for p in sorted(fake.pages))
    assert data.startswith(ndef.wrap_tlv(message))
    assert fake.blocks == {}


def test_too_big_for_classic():
    fake = FakePN532(bytes.fromhex("22DD51C1"))
    reader = PN532Reader(pn532=fake)
    reader.read_uid()
    with pytest.raises(TagWriteError, match="holds 720"):
        reader.write_ndef(b"z" * 800)
