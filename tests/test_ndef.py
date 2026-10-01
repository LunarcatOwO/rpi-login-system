from datetime import datetime

import pytest

from nfc_login.services.attendance import UserStats
from nfc_login.tags import ndef
from nfc_login.tags.payload import TagTooSmall, build_message, site_link


def stats(**overrides):
    values = dict(user_id=12, username="taylor", season_name="2026", total_seconds=45_296,
                  rank=3, ranked_users=25, last_sign_in=datetime(2026, 10, 1, 16, 30),
                  last_sign_out=datetime(2026, 10, 1, 18, 5), signed_in=False)
    values.update(overrides)
    return UserStats(**values)


def test_uri_record_uses_https_prefix_code():
    record = ndef.uri_record("https://example.github.io/x/?id=1")
    assert record.payload[0] == 0x04
    assert ndef.record_text(record) == "https://example.github.io/x/?id=1"


def test_message_round_trip_through_tlv():
    records = [ndef.uri_record("https://a.b/"), ndef.text_record("héllo")]
    tlv = ndef.wrap_tlv(ndef.encode_message(records))
    assert tlv[0] == 0x03 and tlv[-1] == 0xFE
    decoded = ndef.decode_message(ndef.unwrap_tlv(tlv + b"\x00" * 8))
    assert [ndef.record_text(r) for r in decoded] == ["https://a.b/", "héllo"]
    assert decoded[0].tnf == ndef.TNF_WELL_KNOWN


def test_header_flags_mark_first_and_last_record():
    msg = ndef.encode_message([ndef.text_record("a"), ndef.text_record("b")])
    first = msg[0]
    assert first & 0x80 and not first & 0x40
    second_offset = 3 + 1 + len(ndef.text_record("a").payload)
    assert msg[second_offset] & 0x40 and not msg[second_offset] & 0x80


def test_long_tlv_length_uses_three_byte_form():
    msg = ndef.encode_message([ndef.text_record("x" * 300)])
    tlv = ndef.wrap_tlv(msg)
    assert tlv[1] == 0xFF and int.from_bytes(tlv[2:4], "big") == len(msg)
    assert ndef.unwrap_tlv(tlv) == msg


def test_full_payload_contents():
    records = ndef.decode_message(build_message(stats(), "https://x.github.io/site/"))
    assert ndef.record_text(records[0]) == "https://x.github.io/site/?id=12"
    text = ndef.record_text(records[1])
    for expected in ["ID: 12", "User: taylor", "Time: 12h 34m", "Rank: #3 of 25",
                     "Last in: 2026-10-01 16:30", "Last out: 2026-10-01 18:05"]:
        assert expected in text


def test_payload_shrinks_to_fit_ntag213():
    capacity = 144  # NTAG213 data area
    message = build_message(stats(), "https://lunarcatowo.github.io/rpi-login-system/", capacity)
    assert len(ndef.wrap_tlv(message)) <= capacity
    texts = [ndef.record_text(r) for r in ndef.decode_message(message)]
    assert any("12|taylor|12h34m|#3" in t for t in texts)


def test_payload_too_small_raises():
    with pytest.raises(TagTooSmall):
        build_message(stats(), "https://lunarcatowo.github.io/rpi-login-system/", 16)


def test_site_link_appends_query():
    assert site_link("https://a.b/?x=1", 5) == "https://a.b/?x=1&id=5"
