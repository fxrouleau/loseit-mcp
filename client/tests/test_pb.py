"""Pure-function tests for the protobuf wire codec. No mocks, no network."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loseit_client.pb import (
    FIXED32,
    FIXED64,
    LEN,
    VARINT,
    Writer,
    decode_varint,
    encode_tag,
    encode_varint,
    f64_from_uint,
    read_message,
)


# ---- varint ----

def test_varint_roundtrip_small():
    for n in [0, 1, 127, 128, 300, 16383, 16384, 2**31 - 1]:
        encoded = encode_varint(n)
        decoded, i = decode_varint(encoded, 0)
        assert decoded == n
        assert i == len(encoded)


def test_varint_negative_sign_extends_to_10_bytes():
    """`protobuf-java`'s writeInt32(-1) encodes to 10 bytes (sign-extended
    to u64), NOT 5 bytes. This is why `UNSET_INT = -1` in bundle.py."""
    encoded = encode_varint(-1)
    assert len(encoded) == 10
    decoded, _ = decode_varint(encoded, 0)
    assert decoded == (1 << 64) - 1


def test_varint_tag_construction():
    assert encode_tag(1, VARINT) == b"\x08"        # field 1, wire 0
    assert encode_tag(2, LEN) == b"\x12"            # field 2, wire 2
    assert encode_tag(7, LEN) == b"\x3a"
    assert encode_tag(15, VARINT) == b"\x78"
    assert encode_tag(16, VARINT) == b"\x80\x01"   # multi-byte field number


# ---- Writer ----

def test_writer_mixed_fields():
    w = Writer()
    w.varint(1, 42)
    w.string(2, "hello")
    w.f64(3, 3.14)
    w.bytes_(4, b"\x01\x02\x03")
    w.submsg(5, Writer().varint(1, 1))

    parsed = read_message(w.build())
    assert parsed[1][0] == 42
    assert parsed[2][0] == b"hello"
    assert f64_from_uint(parsed[3][0]) == 3.14
    assert parsed[4][0] == b"\x01\x02\x03"
    assert read_message(parsed[5][0])[1][0] == 1


def test_writer_repeated_fields():
    w = Writer()
    w.submsg(13, Writer().string(1, "fat").f64(2, 10.0))
    w.submsg(13, Writer().string(1, "protein").f64(2, 20.0))
    parsed = read_message(w.build())
    assert len(parsed[13]) == 2
    first = read_message(parsed[13][0])
    second = read_message(parsed[13][1])
    assert first[1][0] == b"fat"
    assert f64_from_uint(second[2][0]) == 20.0


def test_f64_from_uint_known_values():
    # 1.0 → 0x3FF0000000000000
    one = struct.unpack("<Q", struct.pack("<d", 1.0))[0]
    assert f64_from_uint(one) == 1.0
    # -1.0 → 0xBFF0000000000000
    neg = struct.unpack("<Q", struct.pack("<d", -1.0))[0]
    assert f64_from_uint(neg) == -1.0


# ---- read_message ----

def test_read_message_multiple_wire_types():
    # field 1 varint, field 2 length-delimited, field 3 fixed64, field 4 fixed32
    data = (
        encode_tag(1, VARINT) + encode_varint(100)
        + encode_tag(2, LEN) + encode_varint(5) + b"hello"
        + encode_tag(3, FIXED64) + struct.pack("<d", 2.5)
        + encode_tag(4, FIXED32) + struct.pack("<I", 7)
    )
    parsed = read_message(data)
    assert parsed[1][0] == 100
    assert parsed[2][0] == b"hello"
    assert f64_from_uint(parsed[3][0]) == 2.5
    assert parsed[4][0] == 7


def test_read_message_empty():
    assert read_message(b"") == {}
