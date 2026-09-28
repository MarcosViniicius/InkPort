"""MOBI/AZW cover extraction (EXTH record 201)."""

from __future__ import annotations

import struct
from pathlib import Path


def extract_mobi_cover(path: Path) -> bytes | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data[60:68] != b"BOOKMOBI":
        return None
    try:
        return _read_exth_cover(data)
    except Exception:
        return None


def _read_exth_cover(data: bytes) -> bytes | None:
    record_count = struct.unpack(">H", data[76:78])[0]
    offsets: list[int] = []
    pos = 78
    for _ in range(record_count):
        offsets.append(struct.unpack(">I", data[pos:pos + 4])[0])
        pos += 8
    offsets.append(len(data))

    record0 = data[offsets[0]:offsets[1]]
    mobi = record0.find(b"MOBI")
    if mobi < 0:
        return None

    exth_flag = struct.unpack(">I", record0[mobi + 4 + 80: mobi + 4 + 84])[0]
    if not exth_flag & 0x40:
        return None

    exth_offset = struct.unpack(">I", record0[mobi + 4 + 20: mobi + 4 + 24])[0]
    exth = mobi + 4 + exth_offset
    if record0[exth:exth + 4] != b"EXTH":
        return None

    count = struct.unpack(">I", record0[exth + 8:exth + 12])[0]
    pointer = exth + 12
    for _ in range(count):
        rtype, rlen = struct.unpack(">II", record0[pointer:pointer + 8])
        if rtype == 201:
            index = struct.unpack(">I", record0[pointer + 8:pointer + 12])[0] + 1
            if index + 1 < len(offsets):
                return data[offsets[index]:offsets[index + 1]]
            return None
        pointer += rlen
    return None
