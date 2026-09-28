"""Isolated, self-cleaning scratch directories for conversions."""

from __future__ import annotations

import contextlib
import os
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

from app.config import get_settings


@contextlib.contextmanager
def temp_workdir(prefix: str = "job_") -> Iterator[Path]:
    """Create an isolated temp folder and always remove it afterwards."""
    settings = get_settings()
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    token = os.urandom(8).hex()
    workdir = settings.temp_dir / f"{prefix}{token}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        yield workdir
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def clean_temp_dir(max_age_seconds: int = 3600) -> int:
    """Delete stale temp folders left behind by interrupted conversions."""
    settings = get_settings()
    if not settings.temp_dir.exists():
        return 0
    now = time.time()
    removed = 0
    for child in settings.temp_dir.iterdir():
        try:
            if now - child.stat().st_mtime <= max_age_seconds:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed
