"""Filesystem layout and safe path handling.

The library lives under ``DATA_DIR/library``. Books store a *relative* path, so
the whole data directory can be moved or backed up without touching the DB.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from app.config import get_settings

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_SPACE = re.compile(r"\s+")
_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, max_len: int = 80) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    return _SLUG.sub("-", value).strip("-")[:max_len] or "item"


def slugify_path(value: str, *, max_len: int = 80) -> str:
    """Slugify a hierarchical name, keeping the separators.

    ``"rss/Meu Feed"`` -> ``"rss/meu-feed"`` so the category becomes nested
    folders in the library instead of one flat ``rss-meu-feed`` directory.
    """
    parts = [slugify(part, max_len=max_len) for part in (value or "").split("/") if part.strip()]
    return "/".join(parts) or "item"


def safe_filename(name: str, *, max_len: int = 180) -> str:
    """Return a filesystem-safe single name component (extension preserved)."""
    name = unicodedata.normalize("NFKC", name or "").strip()
    name = name.replace("\\", "/").split("/")[-1]
    name = _UNSAFE.sub("_", name)
    name = _MULTI_SPACE.sub(" ", name).strip(" .")
    if not name:
        return "arquivo"
    if len(name) <= max_len:
        return name
    stem, dot, ext = name.rpartition(".")
    if dot and len(ext) <= 10:
        return f"{stem[: max_len - len(ext) - 1]}.{ext}"
    return name[:max_len]


def build_library_relpath(category: str | None, filename: str) -> str:
    """``<category-slug>/<filename>`` -- a stable, human-readable layout.

    A category like ``rss/akitaonrails`` becomes nested folders.
    """
    filename = safe_filename(filename)
    return f"{slugify_path(category)}/{filename}" if category else filename


def resolve_library_path(rel_path: str) -> Path:
    """Resolve a stored relative path, guaranteeing it stays inside the library."""
    root = get_settings().library_dir.resolve()
    candidate = (root / rel_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Path escapes the library: {rel_path!r}")
    return candidate


def ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def resolve_cover_path(name: str | None) -> Path | None:
    """Resolve a cover filename to an absolute path inside the covers folder."""
    if not name:
        return None
    path = get_settings().covers_dir / Path(name).name
    return path if path.exists() else None


def human_size(num_bytes: int | float) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
