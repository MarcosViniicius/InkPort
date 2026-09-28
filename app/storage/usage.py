"""Disk usage accounting for the library and the storage limit."""

from __future__ import annotations

from app.config import get_settings


def library_usage() -> dict:
    settings = get_settings()
    total = 0
    count = 0
    by_dir: dict[str, int] = {}

    if settings.library_dir.exists():
        for path in settings.library_dir.rglob("*"):
            if not path.is_file():
                continue
            size = path.stat().st_size
            total += size
            count += 1
            top = path.relative_to(settings.library_dir).parts[0]
            by_dir[top] = by_dir.get(top, 0) + size

    limit = int(settings.storage_limit_gb * 1024**3)
    return {
        "total_bytes": total,
        "file_count": count,
        "limit_bytes": limit,
        "percent": round(total / limit * 100, 1) if limit else 0.0,
        "by_dir": by_dir,
    }


def has_room(extra_bytes: int) -> bool:
    """Check the soft storage ceiling before accepting a new file."""
    settings = get_settings()
    if settings.storage_limit_gb <= 0:
        return True
    usage = library_usage()
    return usage["total_bytes"] + extra_bytes <= usage["limit_bytes"]
