"""File hashing used for de-duplication and change detection."""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024


def sha256_file(path: Path, *, chunk_size: int = _CHUNK) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def quick_hash(path: Path, *, sample: int = 256 * 1024) -> str:
    """Cheap hash of size + head + tail. Used to skip known RSS items fast."""
    stat = path.stat()
    digest = hashlib.sha1()
    digest.update(str(stat.st_size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(sample))
        if stat.st_size > sample:
            handle.seek(max(0, stat.st_size - sample))
            digest.update(handle.read(sample))
    return digest.hexdigest()


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
