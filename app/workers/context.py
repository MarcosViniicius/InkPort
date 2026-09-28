"""Immutable per-job inputs, assembled in the main thread before conversion."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.database.models import Book, ConversionJob
from app.devices.profile import DeviceProfile
from app.devices.registry import get_profile
from app.library.detect import detect
from app.library.detection import Detection
from app.metadata.model import BookMetadata
from app.storage.paths import resolve_cover_path, resolve_library_path


@dataclass(slots=True)
class JobContext:
    job_id: str
    book_id: str
    book_title: str
    source_path: Path
    detection: Detection
    metadata: BookMetadata
    profile: DeviceProfile
    target_format: str
    options: dict = field(default_factory=dict)
    cover_path: Path | None = None
    keep_original: bool = True


def build_context(session: Session, job: ConversionJob) -> JobContext:
    book = session.get(Book, job.book_id)
    if book is None:
        raise LookupError("Livro do job não encontrado.")

    source = resolve_library_path(book.file_path)
    if not source.exists():
        raise FileNotFoundError(f"Arquivo ausente: {book.file_path}")

    detection = detect(source)
    profile = get_profile(job.device_profile, session)

    options = dict(job.options or {})
    # Web-page conversions need the origin URL to resolve relative links/images.
    options.setdefault("source_url", book.source_url or "")

    return JobContext(
        job_id=job.id,
        book_id=book.id,
        book_title=book.title,
        source_path=source,
        detection=detection,
        metadata=_book_metadata(book, detection),
        profile=profile,
        target_format=job.target_format,
        options=options,
        cover_path=resolve_cover_path(book.cover_path),
        keep_original=job.keep_original,
    )


def _book_metadata(book: Book, detection: Detection) -> BookMetadata:
    """Use the *stored* metadata so panel edits survive conversion."""
    return BookMetadata(
        title=book.title,
        author=book.author,
        series=book.series,
        series_index=book.series_index,
        publisher=book.publisher,
        language=book.language or "pt",
        description=book.description,
        isbn=book.isbn,
        published=book.published,
        page_count=book.page_count or detection.page_count,
        reading_direction=book.reading_direction
        or ("rtl" if detection.is_comic else "ltr"),
        age_rating=book.age_rating,
        content_type=detection.content_type,
        media_type=detection.media_type,
        format=detection.format,
        tags=[tag.name for tag in book.tags],
    )
