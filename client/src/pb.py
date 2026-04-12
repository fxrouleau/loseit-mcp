"""Minimal protobuf wire-format reader/writer. No protoc, no runtime dep.

Wire types:
  0 = varint
  1 = fixed64 (8 bytes, little-endian)
  2 = length-delimited
  5 = fixed32 (4 bytes, little-endian)
"""
from __future__ import annotations
import struct
from typing import Any, Iterable

VARINT = 0
FIXED64 = 1
LEN = 2
FIXED32 = 5


def encode_varint(n: int) -> bytes:
    if n < 0:
        n &= (1 << 64) - 1
    out = bytearray()
    while True:
        byte = n & 0x7f
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def encode_tag(field: int, wire_type: int) -> bytes:
    return encode_varint((field << 3) | wire_type)


def encode_f64(f: float) -> bytes:
    return struct.pack("<d", f)


def encode_bool(b: bool) -> bytes:
    return encode_varint(1 if b else 0)


def decode_varint(b: bytes, i: int) -> tuple[int, int]:
    n = 0
    shift = 0
    while True:
        byte = b[i]
        i += 1
        n |= (byte & 0x7f) << shift
        if not (byte & 0x80):
            return n, i
        shift += 7


class Writer:
    """Builds a protobuf message from Python field/value pairs."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def varint(self, field: int, value: int) -> "Writer":
        self._buf += encode_tag(field, VARINT) + encode_varint(value)
        return self

    def f64(self, field: int, value: float) -> "Writer":
        self._buf += encode_tag(field, FIXED64) + encode_f64(value)
        return self

    def bytes_(self, field: int, value: bytes) -> "Writer":
        self._buf += encode_tag(field, LEN) + encode_varint(len(value)) + value
        return self

    def string(self, field: int, value: str) -> "Writer":
        return self.bytes_(field, value.encode("utf-8"))

    def submsg(self, field: int, value: bytes | "Writer") -> "Writer":
        if isinstance(value, Writer):
            value = value.build()
        return self.bytes_(field, value)

    def raw(self, data: bytes) -> "Writer":
        self._buf += data
        return self

    def build(self) -> bytes:
        return bytes(self._buf)


# ---------- schemaless reader (used for parsing responses) ----------

def read_message(b: bytes) -> dict[int, list[Any]]:
    """Parse a message into {field_num: [values,...]}.

    Values are:
      varint/fixed64/fixed32 → int
      length-delimited → bytes (caller decides if it's a sub-msg/string)
    """
    out: dict[int, list[Any]] = {}
    i = 0
    while i < len(b):
        tag, i = decode_varint(b, i)
        field = tag >> 3
        wt = tag & 7
        if wt == VARINT:
            v, i = decode_varint(b, i)
            out.setdefault(field, []).append(v)
        elif wt == FIXED64:
            v = struct.unpack_from("<Q", b, i)[0]
            i += 8
            out.setdefault(field, []).append(v)
        elif wt == LEN:
            L, i = decode_varint(b, i)
            data = b[i : i + L]
            i += L
            out.setdefault(field, []).append(data)
        elif wt == FIXED32:
            v = struct.unpack_from("<I", b, i)[0]
            i += 4
            out.setdefault(field, []).append(v)
        else:
            raise ValueError(f"unsupported wire type {wt}")
    return out


def f64_from_uint(u: int) -> float:
    return struct.unpack("<d", struct.pack("<Q", u))[0]
