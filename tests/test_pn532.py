"""PN532Reader against a fake PN532, for NTAG and MIFARE Classic cards."""

from __future__ import annotations

import pytest

from nfc_login.hardware.nfc_reader import (
    CLASSIC_CAPACITY,
    CLASSIC_KEY,
    PN532Reader,
    TagReadError,
    TagWriteError,
)
from nfc_login.tags import ndef


class FakePN532:
    """A card on a PN532. ``flaky`` maps a page/block to how many times it fails
    first. Like a real card, after a failed command it ignores everything until
    it's selected again (and a MIFARE Classic sector must be unlocked again)."""

    def __init__(self, uid: bytes, cc: bytes | None = None, key: bytes = CLASSIC_KEY,
                 changed_sectors: tuple[int, ...] = (), flaky: dict[int, int] | None = None):
        self.uid = uid
        self.cc = cc
        self.key = key
        self.changed_sectors = changed_sectors
        self.flaky = dict(flaky or {})
        self.pages: dict[int, bytes] = {}
        self.blocks: dict[int, bytes] = {}
        self.authed_sector = None
        self.halted = False
        self.present = True
        self.selects = 0
        self.reads: list[int] = []

    def _check(self, n):
        if self.halted or not self.present:
            return False
        if self.flaky.get(n):
            self.flaky[n] -= 1
            self.halted = True
            self.authed_sector = None
            return False
        return True

    def read_passive_target(self, timeout):
        if not self.present:
            return None
        self.selects += 1
        self.halted = False
        self.authed_sector = None
        return bytearray(self.uid)

    def ntag2xx_read_block(self, page):
        if self.cc is None:
            raise RuntimeError("not a Type 2 tag")
        if not self._check(page):
            return None
        self.reads.append(page)
        return self.cc if page == 3 else self.pages.get(page, b"\x00" * 4)

    def ntag2xx_write_block(self, page, data):
        assert len(data) == 4
        if not self._check(page):
            return False
        self.pages[page] = bytes(data)
        return True

    def mifare_classic_authenticate_block(self, uid, block, key_number, key):
        assert bytes(uid) == self.uid and key_number == 0x60
        if self.halted or not self.present:
            return False
        ok = bytes(key) == self.key and block // 4 not in self.changed_sectors
        self.authed_sector = block // 4 if ok else None
        self.halted = not ok
        return ok

    def mifare_classic_read_block(self, block):
        if not self._check(block):
            return None
        assert self.authed_sector == block // 4
        self.reads.append(block)
        return self.blocks.get(block, b"\x00" * 16)

    def mifare_classic_write_block(self, block, data):
        assert len(data) == 16
        assert block % 4 != 3, "never write a sector trailer"
        assert block >= 4, "never write sector 0"
        if not self._check(block):
            return False
        assert self.authed_sector == block // 4
        self.blocks[block] = bytes(data)
        return True


def classic_bytes(fake: FakePN532) -> bytes:
    return b"".join(fake.blocks[b] for b in sorted(fake.blocks))


def test_classic_card_from_the_legacy_system_is_written():
    fake = FakePN532(bytes.fromhex("22DD51C1"))      # a MIFARE Classic 1K from the legacy system
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


NTAG215_CC = bytes([0xE1, 0x10, 0x3E, 0x00])


def test_ntag_page_that_fails_twice_is_retried():
    fake = FakePN532(bytes.fromhex("04634A2A772681"), cc=NTAG215_CC, flaky={6: 2})
    reader = PN532Reader(pn532=fake)
    reader.read_uid()
    message = b"\xd1" + b"y" * 20
    reader.write_ndef(message)
    data = b"".join(fake.pages[p] for p in sorted(fake.pages))
    assert data.startswith(ndef.wrap_tlv(message))
    assert fake.selects == 3                     # the first read plus one per failure


def test_classic_block_that_fails_is_unlocked_again_and_retried():
    fake = FakePN532(bytes.fromhex("22DD51C1"), flaky={9: 3})
    reader = PN532Reader(pn532=fake)
    reader.read_uid()
    message = ndef.encode_message([ndef.text_record("A007 Taylor " + "x" * 120)])
    reader.write_ndef(message)
    assert ndef.unwrap_tlv(classic_bytes(fake)) == message


def test_gives_up_after_four_tries():
    fake = FakePN532(bytes.fromhex("04634A2A772681"), cc=NTAG215_CC, flaky={5: 4})
    reader = PN532Reader(pn532=fake)
    reader.read_uid()
    with pytest.raises(TagWriteError, match="page 5"):
        reader.write_ndef(b"\xd1" + b"y" * 20)


def test_card_taken_away_stops_at_once():
    fake = FakePN532(bytes.fromhex("04634A2A772681"), cc=NTAG215_CC, flaky={5: 1})
    reader = PN532Reader(pn532=fake)
    reader.read_uid()
    fake.ntag2xx_read_block(3)                   # capacity check still works
    real_check = fake._check

    def gone(n):
        ok = real_check(n)
        if not ok:
            fake.present = False                 # it failed because the card left
        return ok
    fake._check = gone
    with pytest.raises(TagWriteError, match="page 5"):
        reader.write_ndef(b"\xd1" + b"y" * 20)
    assert fake.selects == 1


def test_read_ntag_with_retries_and_stop_at_the_end_of_the_message():
    fake = FakePN532(bytes.fromhex("04634A2A772681"), cc=NTAG215_CC)
    reader = PN532Reader(pn532=fake)
    reader.read_uid()
    message = ndef.encode_message([ndef.uri_record("https://example.github.io/?id=A007")])
    reader.write_ndef(message)
    fake.flaky = {5: 2, 7: 1}
    fake.reads.clear()
    assert reader.read_ndef() == message
    pages = sorted(set(p for p in fake.reads if p != 3))
    assert pages == list(range(4, 4 + -(-len(ndef.wrap_tlv(message)) // 4)))  # not all 124


def test_read_classic_with_retries():
    fake = FakePN532(bytes.fromhex("22DD51C1"))
    reader = PN532Reader(pn532=fake)
    reader.read_uid()
    message = ndef.encode_message([ndef.text_record("007 Morgan " + "z" * 90)])
    reader.write_ndef(message)
    fake.flaky = {4: 1, 8: 2}
    assert reader.read_ndef() == message


def test_read_blank_card():
    fake = FakePN532(bytes.fromhex("04634A2A772681"), cc=bytes([0xE1, 0x10, 0x12, 0x00]))
    reader = PN532Reader(pn532=fake)
    reader.read_uid()
    with pytest.raises(TagReadError, match="no card info"):
        reader.read_ndef()
