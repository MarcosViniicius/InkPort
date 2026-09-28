"""Importing a file into the library: detect, dedup, catalogue, cover."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import Book, SourceKind
from app.library import repository
from app.library.detect import detect
from app.library.detection import Detection
from app.library.hashing import sha256_file
from app.library.taxonomy import ensure_category, set_tags
from app.metadata.cover import generate_cover
from app.metadata.extractor import extract_metadata
from app.metadata.model import BookMetadata
from app.storage.paths import build_library_relpath, safe_filename
from app.storage.usage import has_room

logger = logging.getLogger(__name__)

STATUS_IMPORTED = "imported"
STATUS_DUPLICATE = "duplicate"
STATUS_UNSUPPORTED = "unsupported"
STATUS_NO_ROOM = "no-room"
STATUS_ERROR = "error"


@dataclass(slots=True)
class ImportOutcome:
    status: str
    book: Book | None = None
    message: str = ""


def import_file(
    session: Session,
    source_path: Path,
    *,
    category: str | None = None,
    source: str = SourceKind.UPLOAD.value,
    source_url: str | None = None,
    source_id: str | None = None,
    device_profile: str | None = None,
    move: bool = False,
    detection: Detection | None = None,
    title_override: str | None = None,
) -> ImportOutcome:
    """Import one file. Never raises for ordinary problems; returns a status."""
    if not source_path.exists():
        return ImportOutcome(STATUS_ERROR, message="Arquivo de origem não encontrado.")

    detection = detection or detect(source_path)
    if detection.content_type == "unknown":
        return ImportOutcome(
            STATUS_UNSUPPORTED, message=f"Formato não suportado: .{detection.format}"
        )

    try:
        file_hash = sha256_file(source_path)
    except OSError as exc:
        return ImportOutcome(STATUS_ERROR, message=f"Falha ao ler o arquivo: {exc}")

    duplicate = repository.find_by_hash(session, file_hash)
    if duplicate is not None:
        return ImportOutcome(
            STATUS_DUPLICATE,
            book=duplicate,
            message=f"Já existe na biblioteca: {duplicate.title}",
        )

    size = source_path.stat().st_size
    if not has_room(size):
        return ImportOutcome(STATUS_NO_ROOM, message="Limite de armazenamento atingido.")

    metadata = extract_metadata(source_path, detection)
    if title_override:
        metadata.title = title_override.strip() or metadata.title
    try:
        stored_path = _store_file(session, source_path, metadata, category, move)
    except OSError as exc:
        return ImportOutcome(STATUS_ERROR, message=f"Falha ao gravar na biblioteca: {exc}")

    try:
        book = _create_book(
            session,
            stored_path,
            detection,
            metadata,
            file_hash=file_hash,
            size=size,
            category=category,
            source=source,
            source_url=source_url,
            source_id=source_id,
            device_profile=device_profile,
        )
    except Exception as exc:  # pragma: no cover - keep the library consistent
        logger.exception("failed to catalogue import")
        return ImportOutcome(STATUS_ERROR, message=f"Falha ao catalogar: {exc}")

    logger.info("book imported", extra={"book_id": book.id, "format": book.format})
    return ImportOutcome(STATUS_IMPORTED, book=book, message="Importado com sucesso.")


def _store_file(
    session: Session,
    source_path: Path,
    metadata: BookMetadata,
    category: str | None,
    move: bool,
) -> str:
    """Copy/move the file into the library and return its relative path."""
    settings = get_settings()
    filename = build_filename(metadata, source_path.suffix.lower())
    rel_path = _unique_relpath(settings.library_dir, category, filename)
    target = settings.library_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)

    if move:
        shutil.move(str(source_path), target)
    else:
        shutil.copy2(source_path, target)
    return rel_path


def _unique_relpath(root: Path, category: str | None, filename: str) -> str:
    rel = build_library_relpath(category, filename)
    candidate = root / rel
    if not candidate.exists():
        return rel
    stem, dot, ext = filename.rpartition(".")
    stem = stem if dot else filename
    ext = ext if dot else ""
    for counter in range(1, 1000):
        suffix = f" ({counter}).{ext}" if ext else f" ({counter})"
        rel = build_library_relpath(category, f"{stem}{suffix}")
        if not (root / rel).exists():
            return rel
    raise OSError("Não foi possível gerar um nome único para o arquivo.")


def build_filename(metadata: BookMetadata, ext: str) -> str:
    parts = []
    if metadata.series and metadata.series_index:
        index = int(metadata.series_index) if float(metadata.series_index).is_integer() else metadata.series_index
        parts.append(f"{metadata.series} - {index:02d}" if isinstance(index, int) else f"{metadata.series} - {index}")
        parts.append(metadata.title)
    else:
        parts.append(metadata.title or "livro")
    if metadata.author:
        parts.append(f"({metadata.author})")
    name = " - ".join(p for p in parts if p)
    return safe_filename(f"{name}{ext}")


def _create_book(
    session: Session,
    rel_path: str,
    detection: Detection,
    metadata: BookMetadata,
    *,
    file_hash: str,
    size: int,
    category: str | None,
    source: str,
    source_url: str | None,
    source_id: str | None,
    device_profile: str | None,
) -> Book:
    settings = get_settings()
    absolute = settings.library_dir / rel_path
    cover_rel: str | None = None
    cover_hash: str | None = None
    cover = generate_cover(
        absolute, settings.covers_dir, detection,
        title=metadata.title, author=metadata.author,
    )
    if cover is not None:
        cover_rel = cover.name
        cover_hash = cover.stem

    category_obj = ensure_category(session, category)
    book = Book(
        title=metadata.title or absolute.stem,
        sort_title=(metadata.title or absolute.stem).lower(),
        author=metadata.author,
        series=metadata.series,
        series_index=metadata.series_index,
        publisher=metadata.publisher,
        language=metadata.language,
        description=metadata.description,
        isbn=metadata.isbn,
        published=metadata.published,
        page_count=metadata.page_count,
        reading_direction=metadata.reading_direction
        or ("rtl" if detection.is_comic else "ltr"),
        age_rating=metadata.age_rating,
        content_type=detection.content_type,
        media_type=detection.media_type,
        format=detection.format,
        category_id=category_obj.id if category_obj else None,
        file_path=rel_path,
        file_size=size,
        file_hash=file_hash,
        cover_path=cover_rel,
        cover_hash=cover_hash,
        source=source,
        source_url=source_url,
        source_id=source_id,
        device_profile=device_profile,
        is_original=True,
    )
    session.add(book)
    session.flush()
    if metadata.tags:
        set_tags(session, book, metadata.tags)
    session.commit()
    return book


def stage_conversion_file(
    session: Session, book: Book, source_file: Path, device_profile: str
) -> str:
    """Copy a freshly converted file into the library; return its relative path.

    The device tag uses square brackets on purpose: the filename parser reads a
    trailing ``(something)`` as the author, and we do not want a device slug to
    leak into the book's metadata.
    """
    settings = get_settings()
    ext = source_file.suffix.lower() or ".bin"
    category = book.category_rel.name if book.category_rel else None
    name = safe_filename(f"{book.title} [{device_profile}]{ext}")
    rel_path = _unique_relpath(settings.library_dir, category, name)
    target = settings.library_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_file, target)
    return rel_path


def link_conversion(
    session: Session,
    origin: Book,
    *,
    stored_path: str,
    detection: Detection,
    metadata: BookMetadata,
    device_profile: str | None,
    keep_original: bool,
) -> Book:
    """Catalogue a converted file as a new book derived from ``origin``."""
    settings = get_settings()
    absolute = settings.library_dir / stored_path
    cover = generate_cover(
        absolute, settings.covers_dir, detection,
        title=metadata.title or origin.title, author=metadata.author or origin.author,
    )

    converted = Book(
        title=metadata.title or origin.title,
        sort_title=(metadata.title or origin.title).lower(),
        author=metadata.author or origin.author,
        series=metadata.series or origin.series,
        series_index=metadata.series_index or origin.series_index,
        publisher=metadata.publisher or origin.publisher,
        language=metadata.language or origin.language,
        description=metadata.description or origin.description,
        page_count=metadata.page_count,
        reading_direction=metadata.reading_direction or origin.reading_direction,
        content_type=detection.content_type,
        media_type=detection.media_type,
        format=detection.format,
        category_id=origin.category_id,
        file_path=stored_path,
        file_size=absolute.stat().st_size,
        file_hash=sha256_file(absolute) if absolute.exists() else None,
        cover_path=cover.name if cover else None,
        cover_hash=cover.stem if cover else None,
        source=SourceKind.IMPORT.value,
        origin_book_id=origin.id,
        # Keep the provenance: without it a later re-import cannot tell that this
        # article is already in the library (the converted file replaced the
        # original, which is where the URL lived).
        source_url=origin.source_url,
        source_id=origin.source_id,
        device_profile=device_profile,
        is_original=False,
    )
    session.add(converted)
    session.flush()
    set_tags(session, converted, [tag.name for tag in origin.tags])
    if not keep_original:
        origin.missing = False
    session.commit()
    return converted
