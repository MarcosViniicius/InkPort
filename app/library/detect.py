"""Content detection, layered and never trusting the extension alone.

1. Extension -> first guess.
2. Magic bytes -> corrects mislabelled files.
3. Container inspection -> image ZIP vs comic ZIP.
4. Embedded metadata -> page count, fixed-layout / scan heuristics.
"""

from __future__ import annotations

from pathlib import Path

from app.library import formats
from app.library.containers import zip_is_comic
from app.library.detection import Detection
from app.library.sniff import resolve_format


def detect(path: Path) -> Detection:
    ext = path.suffix.lower().lstrip(".")
    try:
        with path.open("rb") as handle:
            head = handle.read(4096)
    except OSError:
        head = b""

    fmt, mime, corrected = resolve_format(ext, head)
    content = formats.content_type_for(fmt)
    page_count: int | None = None
    note: str | None = None
    image_only = False

    if fmt in formats.COMIC_ARCHIVE_EXTS or (fmt == "zip" and content == formats.CONTENT_COMIC):
        looks_comic, images = zip_is_comic(path)
        page_count = images or None
        if fmt in formats.COMIC_ARCHIVE_EXTS and not looks_comic:
            note = "Marcado como quadrinho, mas contém poucas imagens."
    elif fmt == "epub":
        from app.metadata.epub import read_epub_metadata

        data = _safe(read_epub_metadata, path)
        if data:
            page_count = data.get("page_count")
            if data.get("has_fixed_layout") or data.get("image_only"):
                note = "EPUB com layout fixo (baseado em imagens)."
            if data.get("image_only"):
                image_only = True
    elif fmt == "pdf":
        from app.metadata.pdf import read_pdf_metadata

        data = _safe(read_pdf_metadata, path)
        if data:
            page_count = data.get("page_count")
            image_only = bool(data.get("image_only_pages"))
            if image_only:
                note = "PDF predominantemente de imagens (digitalizado)."

    return Detection(
        format=fmt,
        media_type=mime,
        content_type=content,
        page_count=page_count,
        note=note,
        corrected=corrected,
        image_only=image_only,
    )


def detect_bytes(data: bytes, filename: str) -> Detection:
    """Fast detection for streamed uploads (no container walk)."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    fmt, mime, corrected = resolve_format(ext, data[:4096])
    return Detection(
        format=fmt,
        media_type=mime,
        content_type=formats.content_type_for(fmt),
        corrected=corrected,
    )


def is_supported(path: Path) -> bool:
    return path.suffix.lower().lstrip(".") in formats.ALL_EXTS


def _safe(func, path: Path) -> dict | None:
    try:
        return func(path)
    except Exception:  # pragma: no cover - corrupt files must not break a scan
        return None
