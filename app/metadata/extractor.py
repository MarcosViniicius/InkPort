"""Metadata orchestration: filename guesses + embedded metadata, merged.

Format-specific readers live in sibling modules; this file only decides which
reader to call and how to merge the result onto the filename guess.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.library.detect import detect
from app.library.detection import Detection
from app.metadata.filenames import parse_filename
from app.metadata.model import BookMetadata

_ISBN = re.compile(r"[^0-9Xx]")


def extract_metadata(path: Path, detection: Detection | None = None) -> BookMetadata:
    detection = detection or detect(path)

    meta = BookMetadata(
        title=path.stem,
        content_type=detection.content_type,
        media_type=detection.media_type,
        format=detection.format,
        page_count=detection.page_count,
        note=detection.note,
    )

    guessed = parse_filename(path.stem)
    meta.title = guessed["title"] or path.stem
    meta.author = guessed["author"]
    meta.series = guessed["series"]
    meta.series_index = guessed["series_index"]
    meta.published = guessed["published"]

    try:
        if detection.format == "epub":
            _apply_epub(path, meta)
        elif detection.format == "pdf":
            _apply_pdf(path, meta)
        elif detection.format in {"cbz", "zip"} and detection.is_comic:
            _apply_comicinfo(path, meta)
        elif detection.is_comic:
            meta.reading_direction = meta.reading_direction or "rtl"
        elif detection.is_image:
            meta.reading_direction = "ltr"
    except Exception as exc:  # pragma: no cover - metadata must never break imports
        meta.note = meta.note or f"Falha ao ler metadados: {exc}"

    return meta


def _apply_epub(path: Path, meta: BookMetadata) -> None:
    from app.metadata.epub import read_epub_metadata

    data = read_epub_metadata(path)
    meta.title = data.get("title") or meta.title
    meta.author = data.get("author") or meta.author
    meta.language = data.get("language")
    meta.description = data.get("description")
    meta.publisher = data.get("publisher")
    meta.isbn = _clean_isbn(data.get("isbn"))
    meta.series = data.get("series") or meta.series
    meta.series_index = data.get("series_index") or meta.series_index
    if data.get("page_count"):
        meta.page_count = data["page_count"]


def _apply_pdf(path: Path, meta: BookMetadata) -> None:
    from app.metadata.pdf import read_pdf_metadata

    data = read_pdf_metadata(path)
    meta.title = _clean(data.get("title")) or meta.title
    meta.author = _clean(data.get("author")) or meta.author
    meta.description = data.get("subject")
    if data.get("page_count"):
        meta.page_count = data["page_count"]


def _apply_comicinfo(path: Path, meta: BookMetadata) -> None:
    from app.metadata.comicinfo import read_comicinfo

    data = read_comicinfo(path) or {}
    meta.title = data.get("title") or meta.title
    meta.author = data.get("author") or meta.author
    meta.series = data.get("series") or meta.series
    meta.series_index = data.get("series_index") or meta.series_index
    meta.publisher = data.get("publisher") or meta.publisher
    meta.language = data.get("language") or meta.language
    meta.description = data.get("description") or meta.description
    meta.age_rating = data.get("age_rating")
    meta.reading_direction = data.get("reading_direction") or "rtl"
    if data.get("page_count"):
        meta.page_count = int(data["page_count"])
    if data.get("genre"):
        meta.tags = [g.strip() for g in data["genre"].split(",") if g.strip()]


def _clean_isbn(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _ISBN.sub("", value)
    return cleaned if 10 <= len(cleaned) <= 13 else None


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.lower().startswith(("unknown", "(c)")):
        return None
    return value or None
