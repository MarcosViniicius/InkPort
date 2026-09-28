"""Mutating operations on library books: edit, rename, move, delete, covers."""

from __future__ import annotations

import contextlib
import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import Book
from app.library.detect import detect
from app.library.importer import build_filename
from app.library.taxonomy import delete_empty_tags, ensure_category, set_tags
from app.metadata.cover import store_cover
from app.metadata.extractor import extract_metadata
from app.storage.paths import build_library_relpath, resolve_library_path, safe_filename

logger = logging.getLogger(__name__)

EDITABLE_FIELDS = {
    "title",
    "sort_title",
    "author",
    "series",
    "series_index",
    "publisher",
    "language",
    "description",
    "isbn",
    "published",
    "page_count",
    "reading_direction",
    "age_rating",
    "content_type",
}


def update_metadata(session: Session, book: Book, values: dict) -> Book:
    """Apply edited metadata. ``category`` and ``tags`` are handled specially."""
    for field in EDITABLE_FIELDS:
        if field in values:
            setattr(book, field, values[field])

    if "category" in values:
        category = ensure_category(session, values.get("category"))
        book.category_id = category.id if category else None

    if "tags" in values:
        tags = values["tags"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        set_tags(session, book, [t for t in tags if t])

    if not book.sort_title and book.title:
        book.sort_title = book.title.lower()

    session.commit()
    return book


def refresh_metadata(session: Session, book: Book) -> Book:
    """Re-read metadata from the stored file (metadata cache refresh)."""
    path = resolve_library_path(book.file_path)
    if not path.exists():
        book.missing = True
        session.commit()
        return book
    detection = detect(path)
    metadata = extract_metadata(path, detection)
    if metadata.title and not book.title:
        book.title = metadata.title
    book.author = book.author or metadata.author
    book.series = book.series or metadata.series
    book.series_index = book.series_index or metadata.series_index
    book.publisher = book.publisher or metadata.publisher
    book.language = book.language or metadata.language
    book.description = book.description or metadata.description
    book.page_count = metadata.page_count or book.page_count
    book.file_size = path.stat().st_size
    session.commit()
    return book


def rename_file(session: Session, book: Book, *, new_name: str | None = None) -> Book:
    """Move the stored file to match its (possibly edited) metadata."""
    settings = get_settings()
    current = resolve_library_path(book.file_path)
    if not current.exists():
        book.missing = True
        session.commit()
        return book

    if new_name:
        filename = safe_filename(new_name)
        if "." not in filename:
            filename = f"{filename}{current.suffix}"
    else:
        metadata = extract_metadata(current, detect(current))
        metadata.title = book.title or metadata.title
        metadata.author = book.author
        metadata.series = book.series
        metadata.series_index = book.series_index
        filename = build_filename(metadata, current.suffix.lower())

    category = book.category_rel.name if book.category_rel else None
    rel_path = build_library_relpath(category, filename)
    target = settings.library_dir / rel_path
    if target.resolve() == current.resolve():
        return book

    target.parent.mkdir(parents=True, exist_ok=True)
    counter = 1
    while target.exists():
        stem, dot, ext = filename.rpartition(".")
        candidate = f"{stem} ({counter}).{ext}" if dot else f"{filename} ({counter})"
        rel_path = build_library_relpath(category, candidate)
        target = settings.library_dir / rel_path
        counter += 1

    shutil.move(str(current), target)
    book.file_path = rel_path
    session.commit()
    logger.info("book file renamed", extra={"book_id": book.id, "path": rel_path})
    return book


def move_to_category(session: Session, book: Book, category_name: str | None) -> Book:
    """Move the file into the folder of another category and relink it."""
    category = ensure_category(session, category_name)
    book.category_id = category.id if category else None
    book.category_rel = category
    session.flush()

    current = resolve_library_path(book.file_path)
    if not current.exists():
        book.missing = True
        session.commit()
        return book

    rel_path = build_library_relpath(
        category.name if category else None, current.name
    )
    target = get_settings().library_dir / rel_path
    if target.resolve() == current.resolve():
        session.commit()
        return book

    target.parent.mkdir(parents=True, exist_ok=True)
    counter = 1
    while target.exists():
        target = target.with_name(f"{target.stem} ({counter}){target.suffix}")
        counter += 1
    shutil.move(str(current), target)
    book.file_path = str(target.relative_to(get_settings().library_dir)).replace("\\", "/")
    session.commit()
    return book


def set_cover(session: Session, book: Book, data: bytes) -> Book:
    cover = store_cover(data, get_settings().covers_dir)
    book.cover_path = cover.name
    book.cover_hash = cover.stem
    session.commit()
    return book


def delete_books(session: Session, books: list[Book], *, delete_files: bool = True) -> int:
    """Delete books (and optionally their files). Returns the count removed."""
    removed = 0
    for book in books:
        if delete_files:
            for candidate in _book_files(book):
                try:
                    candidate.unlink(missing_ok=True)
                except OSError as exc:  # pragma: no cover
                    logger.warning("could not delete file", extra={"error": str(exc)})
        session.delete(book)
        removed += 1
    session.flush()
    delete_empty_tags(session)
    session.commit()
    return removed


def _book_files(book: Book) -> list[Path]:
    paths: list[Path] = []
    with contextlib.suppress(ValueError):
        paths.append(resolve_library_path(book.file_path))
    if book.cover_path:
        paths.append(get_settings().covers_dir / book.cover_path)
    return paths


def mark_missing(session: Session) -> int:
    """Flag books whose files vanished (e.g. after a manual cleanup)."""
    count = 0
    for book in session.query(Book).all():
        try:
            exists = resolve_library_path(book.file_path).exists()
        except ValueError:
            exists = False
        if book.missing != (not exists):
            book.missing = not exists
            count += 1
    if count:
        session.commit()
    return count
