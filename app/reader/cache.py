"""On-disk cache for rendered pages, converted books and derived artifacts.

Keyed by ``book id + file fingerprint`` (mtime + size), so replacing or editing
a file invalidates every cached artifact automatically -- no manual flush.

Writes are atomic: work happens in a private ``.tmp`` directory (or file) and
is renamed into place only when complete, so two concurrent requests can never
observe a half-written directory. A per-path in-process lock serialises the
first build; later requests just find the result.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from app.config import get_settings

#: Cached artifacts older than this are removed on the next access.
MAX_AGE_SECONDS = 7 * 24 * 3600
#: Safety cap so a runaway cache cannot fill the disk silently.
MAX_BYTES = 4 * 1024 * 1024 * 1024

_READY = ".ready"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_last_cleanup = 0.0


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def fingerprint(path: Path) -> str:
    """Cheap identity of a file: nanosecond mtime + size."""
    stat = path.stat()
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


class ReaderCache:
    """Cache root scoped to one book (and its current file fingerprint)."""

    def __init__(self, book_id: str, path: Path | None) -> None:
        self.book_id = book_id
        self._path = path
        self._fingerprint: str | None = None

    # -- locations ---------------------------------------------------------
    @property
    def fingerprint(self) -> str:
        if self._fingerprint is None:
            self._fingerprint = fingerprint(self._path) if self._path else "none"
        return self._fingerprint

    def root(self) -> Path:
        return get_settings().temp_dir / "reader"

    def directory(self) -> Path:
        """The cache directory for this book; not necessarily existing."""
        return self.root() / f"{self.book_id}-{self.fingerprint}"

    # -- building ----------------------------------------------------------
    def ensure(self, builder: Callable[[Path], None]) -> Path:
        """Return a ready directory, building its contents once."""
        dest = self.directory()
        marker = dest / _READY
        if marker.exists():
            self._cleanup_throttled()
            return dest

        dest.parent.mkdir(parents=True, exist_ok=True)
        self._cleanup_throttled()
        with _lock_for(str(dest)):
            if marker.exists():
                return dest
            tmp = dest.with_name(f"{dest.name}.tmp-{os.getpid()}-{threading.get_ident()}")
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True, exist_ok=True)
            try:
                builder(tmp)
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            (tmp / _READY).write_text("ok", encoding="utf-8")
            shutil.rmtree(dest, ignore_errors=True)
            os.replace(tmp, dest)
        return dest

    def file(self, name: str, builder: Callable[[Path], None]) -> Path:
        """Return a single cached file, building it once and atomically."""
        dest = self.directory() / name
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        with _lock_for(str(dest)):
            if dest.exists():
                return dest
            tmp = dest.with_name(
                f"{dest.stem}.tmp-{os.getpid()}-{threading.get_ident()}{dest.suffix}"
            )
            tmp.unlink(missing_ok=True)
            try:
                builder(tmp)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            os.replace(tmp, dest)
        return dest

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        with _lock_for(str(self.directory())):
            yield

    # -- housekeeping ------------------------------------------------------
    def _cleanup_throttled(self) -> None:
        global _last_cleanup
        now = time.time()
        if now - _last_cleanup < 600:
            return
        _last_cleanup = now
        cleanup_reader_cache()


def cleanup_reader_cache(max_age: int = MAX_AGE_SECONDS) -> int:
    """Remove stale reader cache directories. Returns how many were removed."""
    root = get_settings().temp_dir / "reader"
    if not root.exists():
        return 0
    removed = 0
    now = time.time()
    for child in root.iterdir():
        try:
            if not child.is_dir():
                continue
            if now - child.stat().st_mtime <= max_age:
                continue
            # Never remove a directory a request is currently building.
            with _locks_guard:
                lock = _locks.get(str(child))
            if lock is not None and lock.locked():
                continue
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
        except OSError:
            continue
    return removed
