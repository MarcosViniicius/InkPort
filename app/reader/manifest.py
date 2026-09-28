"""Manifest assembly: the JSON the reader UI needs to render a book.

The shape is deliberately uniform across formats: the frontend switches on
``kind`` and reads ``capabilities`` instead of guessing from the file type.
"""

from __future__ import annotations

from app.reader.base import ReaderContext, ReaderHandler

#: Viewport presets always available, independent of device profiles.
SCREEN_PRESETS: list[dict] = [
    {"slug": "mobile", "name": "Celular", "width": 390, "height": 844, "ppi": 0,
     "gray_levels": 256, "color": True, "manga_rtl": False, "group": "Telas"},
    {"slug": "tablet", "name": "Tablet", "width": 820, "height": 1180, "ppi": 0,
     "gray_levels": 256, "color": True, "manga_rtl": False, "group": "Telas"},
    {"slug": "desktop", "name": "Desktop", "width": 1280, "height": 900, "ppi": 0,
     "gray_levels": 256, "color": True, "manga_rtl": False, "group": "Telas"},
]


def serialize_profiles(profiles: dict) -> list[dict]:
    """Turn registry profiles into compact manifest entries."""
    out: list[dict] = []
    for slug, profile in profiles.items():
        if profile.is_universal:
            continue  # no screen size: nothing to preview
        out.append(
            {
                "slug": slug,
                "name": profile.name,
                "brand": profile.brand,
                "width": int(profile.screen_width or 0),
                "height": int(profile.screen_height or 0),
                "ppi": int(profile.ppi or 0),
                "gray_levels": int(profile.gray_levels or 16),
                "color": bool(profile.color),
                "manga_rtl": bool(profile.manga_rtl),
                "group": profile.brand or "E-readers",
            }
        )
    return out


def build_manifest(
    ctx: ReaderContext,
    *,
    handler: ReaderHandler,
    capabilities: dict,
    chapters: list[dict] | None = None,
    page_count: int = 0,
    toc: list[dict] | None = None,
    message: str | None = None,
    state: str = "ready",
    extra: dict | None = None,
) -> dict:
    book = ctx.book
    fmt = (getattr(ctx.detection, "format", None) or ctx.format or "").lower()
    manifest = {
        "state": state,
        "handler": handler.name,
        "kind": handler.name,
        "label": handler.label,
        "native": bool(capabilities.get("native")),
        "capabilities": capabilities,
        "message": message,
        "book": {
            "id": ctx.book_id,
            "title": getattr(book, "title", "") or "",
            "author": getattr(book, "author", None) or "",
            "format": fmt,
            "content_type": getattr(ctx.detection, "content_type", None)
            or getattr(book, "content_type", None)
            or "",
            "media_type": getattr(book, "media_type", None) or "",
            "direction": ctx.direction,
            "page_count": page_count or getattr(book, "page_count", 0) or 0,
            "file_size": getattr(book, "file_size", 0) or 0,
        },
        "direction": ctx.direction,
        "manga_rtl": ctx.manga_rtl,
        "chapters": chapters or [],
        "toc": toc if toc is not None else (chapters or []),
        "pages": page_count,
        "presets": SCREEN_PRESETS,
        "profiles": ctx.profiles,
        "urls": {
            "raw": f"/reader/{ctx.book_id}/raw",
            "page": f"/reader/{ctx.book_id}/page/{{index}}",
            "chapter": f"/reader/{ctx.book_id}/chapter/{{key}}",
            "asset": f"/reader/{ctx.book_id}/asset/{{key}}",
            "download": f"/library/{ctx.book_id}/file",
            "back": f"/library/{ctx.book_id}",
        },
        "defaults": {
            "gray_levels": int(getattr(ctx.profile, "gray_levels", 16) or 16),
            "width": int(getattr(ctx.profile, "screen_width", 0) or 0),
            "height": int(getattr(ctx.profile, "screen_height", 0) or 0),
        },
    }
    if extra:
        manifest.update(extra)
    return manifest
