# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Minimal NDEF encoder/decoder for NFC Forum Type 2 tags (NTAG213/215/216)."""

# NDEF is the standard format phones read from NFC cards. A message is a list of
# records; we only need URI ("U", a link) and Text ("T") records. On the card the
# message sits inside a TLV block (Type, Length, Value) starting at page 4.

from __future__ import annotations

from dataclasses import dataclass

TNF_WELL_KNOWN = 0x01  # "type name format": U and T are NFC Forum well-known types

# URI identifier codes from the NFC Forum URI RTD (most specific first).
URI_PREFIXES = [
    (0x02, "https://www."),
    (0x01, "http://www."),
    (0x04, "https://"),
    (0x03, "http://"),
]

TLV_NDEF = 0x03        # "an NDEF message follows"
TLV_TERMINATOR = 0xFE  # "nothing more on this card"


@dataclass(frozen=True)
class Record:
    """One NDEF record: its type (b"U" or b"T") and its data."""

    type: bytes
    payload: bytes
    tnf: int = TNF_WELL_KNOWN


def uri_record(uri: str) -> Record:
    """A link record. A common start like "https://" is saved as one code byte."""
    for code, prefix in URI_PREFIXES:
        if uri.startswith(prefix):
            return Record(b"U", bytes([code]) + uri[len(prefix):].encode())
    return Record(b"U", b"\x00" + uri.encode())  # 0x00: no prefix shortened


def text_record(text: str, lang: str = "en") -> Record:
    """A plain text record, tagged with its language."""
    lang_bytes = lang.encode("ascii")
    # Status byte: bit 7 = 0 (UTF-8), low 6 bits = language code length.
    return Record(b"T", bytes([len(lang_bytes)]) + lang_bytes + text.encode("utf-8"))


def encode_message(records: list[Record]) -> bytes:
    """Records -> NDEF message bytes. Each record: header, type length,
    payload length, type, payload."""
    if not records:
        raise ValueError("an NDEF message needs at least one record")
    out = bytearray()
    for i, record in enumerate(records):
        header = record.tnf & 0x07  # low 3 bits: the type name format
        if i == 0:
            header |= 0x80  # MB: message begin
        if i == len(records) - 1:
            header |= 0x40  # ME: message end
        short = len(record.payload) < 256  # short records use 1 length byte, not 4
        if short:
            header |= 0x10  # SR: short record
        out.append(header)
        out.append(len(record.type))
        if short:
            out.append(len(record.payload))
        else:
            out += len(record.payload).to_bytes(4, "big")
        out += record.type
        out += record.payload
    return bytes(out)


def wrap_tlv(message: bytes) -> bytes:
    """NDEF Message TLV followed by a Terminator TLV, as stored on the tag."""
    if len(message) < 0xFF:
        length = bytes([len(message)])                       # 1-byte length
    else:
        length = b"\xff" + len(message).to_bytes(2, "big")  # 0xFF, then a 2-byte length
    return bytes([TLV_NDEF]) + length + message + bytes([TLV_TERMINATOR])


def decode_message(data: bytes) -> list[Record]:
    """Decode a raw NDEF message (no TLV wrapper). Used by tests and tools."""
    records = []
    i = 0
    while i < len(data):
        header = data[i]
        type_len = data[i + 1]
        i += 2
        if header & 0x10:  # SR: short record
            payload_len = data[i]
            i += 1
        else:
            payload_len = int.from_bytes(data[i:i + 4], "big")
            i += 4
        id_len = 0
        if header & 0x08:  # IL: the record has an ID field (we skip it)
            id_len = data[i]
            i += 1
        rtype = data[i:i + type_len]
        i += type_len + id_len
        payload = data[i:i + payload_len]
        i += payload_len
        records.append(Record(rtype, payload, header & 0x07))
        if header & 0x40:  # ME: last record
            break
    return records


def unwrap_tlv(data: bytes) -> bytes:
    """Find the NDEF Message TLV in tag memory and return its message bytes."""
    i = 0
    while i < len(data):
        tag = data[i]
        if tag == 0x00:          # NULL TLV
            i += 1
            continue
        if tag == TLV_TERMINATOR:
            break
        length = data[i + 1]
        i += 2
        if length == 0xFF:
            length = int.from_bytes(data[i:i + 2], "big")
            i += 2
        if tag == TLV_NDEF:
            return bytes(data[i:i + length])
        i += length
    raise ValueError("no NDEF message found")


def tlv_end(data: bytes) -> int | None:
    """Where the NDEF message ends in tag memory, so a reader can stop reading there."""
    # None means "data is too short to tell yet": read another block and ask again.
    i = 0
    while i < len(data):
        tag = data[i]
        if tag == 0x00:
            i += 1
            continue
        if tag == TLV_TERMINATOR:
            return i + 1
        if i + 1 >= len(data):
            return None
        length = data[i + 1]
        header = 2
        if length == 0xFF:
            if i + 3 >= len(data):
                return None
            length = int.from_bytes(data[i + 2:i + 4], "big")
            header = 4
        if tag == TLV_NDEF:
            return i + header + length
        i += header + length
    return None


def record_text(record: Record) -> str:
    """Human-readable value of a URI or Text record."""
    if record.type == b"U":
        code = record.payload[0]
        prefix = dict(URI_PREFIXES).get(code, "")
        return prefix + record.payload[1:].decode()
    if record.type == b"T":
        lang_len = record.payload[0] & 0x3F  # skip the status byte and language code
        return record.payload[1 + lang_len:].decode("utf-8")
    return record.payload.hex()
