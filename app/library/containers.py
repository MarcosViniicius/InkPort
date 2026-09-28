"""Low-level archive helpers (ZIP-based containers).

Deliberately ZIP-only; RAR/7z need an external tool and therefore live in the
converter layer, where tool availability is already handled.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from app.library.formats import IMAGE_EXTS

_DIGITS = re.compile(r"(\d+)")


def natural_key(name: str):
    """Sort key so ``page2`` comes before ``page10``."""
    return [int(part) if part.isdigit() else part.lower() for part in _DIGITS.split(name)]


def zip_image_names(path: Path) -> list[str]:
    """Sorted image member names inside a ZIP/CBZ archive."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = [
                n for n in zf.namelist()
                if not n.endswith("/") and n.rsplit(".", 1)[-1].lower() in IMAGE_EXTS
            ]
    except (zipfile.BadZipFile, OSError):
        return []
    return sorted(names, key=natural_key)


def zip_is_comic(path: Path) -> tuple[bool, int]:
    """Return (looks_like_comic, image_count) for a ZIP container."""
    try:
        with zipfile.ZipFile(path) as zf:
            entries = [n for n in zf.namelist() if not n.endswith("/")]
    except (zipfile.BadZipFile, OSError):
        return False, 0
    images = [n for n in entries if n.rsplit(".", 1)[-1].lower() in IMAGE_EXTS]
    if not images:
        return False, 0
    return len(images) >= max(1, len(entries) * 0.5), len(images)


def extract_zip_images(path: Path, dest: Path) -> list[Path]:
    """Extract every image from a ZIP into ``dest``, numbered in natural order."""
    dest.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    names = zip_image_names(path)
    with zipfile.ZipFile(path) as zf:
        for index, name in enumerate(names, start=1):
            ext = name.rsplit(".", 1)[-1].lower()
            target = dest / f"{index:05d}.{ext}"
            with zf.open(name) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(target)
    return extracted
