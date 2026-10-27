"""Steam binary VDF (shortcuts.vdf): strict, lossless reader and writer.

Anything unexpected raises VdfError instead of being guessed at, so callers can leave the file alone.
"""

from __future__ import annotations

import struct

T_MAP, T_STR, T_INT, T_U64, T_END = 0x00, 0x01, 0x02, 0x07, 0x08


class VdfError(Exception):
    pass


class Int32(int):
    """A 32-bit little-endian integer value (type 0x02)."""


class UInt64(int):
    """A 64-bit little-endian unsigned integer value (type 0x07)."""


def _cstr(buf: bytes, pos: int) -> tuple[str, int]:
    end = buf.find(b"\x00", pos)
    if end < 0:
        raise VdfError("unterminated string")
    return buf[pos:end].decode("utf-8", "surrogateescape"), end + 1


def _read_map(buf: bytes, pos: int, depth: int = 0) -> tuple[dict, int]:
    if depth > 16:
        raise VdfError("nesting too deep")
    out: dict = {}
    while True:
        if pos >= len(buf):
            raise VdfError("truncated: missing end marker")
        kind = buf[pos]
        pos += 1
        if kind == T_END:
            return out, pos
        key, pos = _cstr(buf, pos)
        if key in out:
            raise VdfError(f"duplicate key {key!r}")
        if kind == T_MAP:
            out[key], pos = _read_map(buf, pos, depth + 1)
        elif kind == T_STR:
            out[key], pos = _cstr(buf, pos)
        elif kind == T_INT:
            if pos + 4 > len(buf):
                raise VdfError("truncated int32")
            out[key] = Int32(struct.unpack_from("<i", buf, pos)[0])
            pos += 4
        elif kind == T_U64:
            if pos + 8 > len(buf):
                raise VdfError("truncated uint64")
            out[key] = UInt64(struct.unpack_from("<Q", buf, pos)[0])
            pos += 8
        else:
            raise VdfError(f"unsupported value type 0x{kind:02x} for key {key!r}")


def loads(buf: bytes) -> dict:
    data, pos = _read_map(buf, 0)
    if pos != len(buf):
        raise VdfError("trailing data after the root map")
    return data


def _write_map(data: dict, out: bytearray) -> None:
    for key, value in data.items():
        kb = key.encode("utf-8", "surrogateescape")
        if b"\x00" in kb:
            raise VdfError("NUL in key")
        if isinstance(value, dict):
            out += bytes([T_MAP]) + kb + b"\x00"
            _write_map(value, out)
        elif isinstance(value, str):
            vb = value.encode("utf-8", "surrogateescape")
            if b"\x00" in vb:
                raise VdfError("NUL in value")
            out += bytes([T_STR]) + kb + b"\x00" + vb + b"\x00"
        elif isinstance(value, UInt64):
            out += bytes([T_U64]) + kb + b"\x00" + struct.pack("<Q", value)
        elif isinstance(value, int):
            out += bytes([T_INT]) + kb + b"\x00" + struct.pack("<i", value)
        else:
            raise VdfError(f"cannot write {type(value).__name__}")
    out.append(T_END)


def dumps(data: dict) -> bytes:
    out = bytearray()
    _write_map(data, out)
    return bytes(out)
