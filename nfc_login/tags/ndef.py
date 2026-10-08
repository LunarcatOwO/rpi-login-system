# rpi-login-system: NFC sign-in kiosk for a Raspberry Pi 4 B
# Created by LunarcatOwO (https://github.com/LunarcatOwO)
# Copyright (C) 2026 LunarcatOwO
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It comes WITHOUT ANY WARRANTY; see the LICENSE file.

"""Minimal NDEF encoder/decoder for NFC Forum Type 2 tags (NTAG213/215/216).

Only what this project needs: well-known URI ("U") and Text ("T") records,
wrapped in the TLV block that Type 2 tags store from page 4 onwards.
"""

from __future__ import annotations

from dataclasses import dataclass

TNF_WELL_KNOWN = 0x01

# URI identifier codes from the NFC Forum URI RTD (most specific first).
URI_PREFIXES = [
    (0x02, "https://www."),
    (0x01, "http://www."),
    (0x04, "https://"),
    (0x03, "http://"),
]

TLV_NDEF = 0x03
TLV_TERMINATOR = 0xFE


@dataclass(frozen=True)
class Record:
    type: bytes
    payload: bytes
    tnf: int = TNF_WELL_KNOWN


def uri_record(uri: str) -> Record:
    for code, prefix in URI_PREFIXES:
        if uri.startswith(prefix):
            return Record(b"U", bytes([code]) + uri[len(prefix):].encode())
    return Record(b"U", b"\x00" + uri.encode())


def text_record(text: str, lang: str = "en") -> Record:
    lang_bytes = lang.encode("ascii")
    # Status byte: bit 7 = 0 (UTF-8), low 6 bits = language code length.
    return Record(b"T", bytes([len(lang_bytes)]) + lang_bytes + text.encode("utf-8"))


def encode_message(records: list[Record]) -> bytes:
    if not records:
        raise ValueError("an NDEF message needs at least one record")
    out = bytearray()
    for i, record in enumerate(records):
        header = record.tnf & 0x07
        if i == 0:
            header |= 0x80  # MB: message begin
        if i == len(records) - 1:
            header |= 0x40  # ME: message end
        short = len(record.payload) < 256
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
        length = bytes([len(message)])
    else:
        length = b"\xff" + len(message).to_bytes(2, "big")
    return bytes([TLV_NDEF]) + length + message + bytes([TLV_TERMINATOR])


def decode_message(data: bytes) -> list[Record]:
    """Decode a raw NDEF message (no TLV wrapper). Used by tests and tools."""
    records = []
    i = 0
    while i < len(data):
        header = data[i]
        type_len = data[i + 1]
        i += 2
        if header & 0x10:
            payload_len = data[i]
            i += 1
        else:
            payload_len = int.from_bytes(data[i:i + 4], "big")
            i += 4
        id_len = 0
        if header & 0x08:
            id_len = data[i]
            i += 1
        rtype = data[i:i + type_len]
        i += type_len + id_len
        payload = data[i:i + payload_len]
        i += payload_len
        records.append(Record(rtype, payload, header & 0x07))
        if header & 0x40:
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
    """How many bytes from the start of tag memory hold the NDEF message TLV,
    so a reader can stop there. None if ``data`` is too short to tell yet."""
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
        lang_len = record.payload[0] & 0x3F
        return record.payload[1 + lang_len:].decode("utf-8")
    return record.payload.hex()
