"""JSON serialisation for the internal REST API."""

from __future__ import annotations

from app.database.models import Book, ConversionJob, Feed, FeedItem
from app.library.formats import media_type_for


def book_summary(book: Book) -> dict:
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author,
        "series": book.series,
        "series_index": book.series_index,
        "format": book.format,
        "content_type": book.content_type,
        "media_type": media_type_for(book.format, book.media_type),
        "file_size": book.file_size,
        "page_count": book.page_count,
        "language": book.language,
        "category": book.category_rel.name if book.category_rel else None,
        "tags": book.tag_names,
        "source": book.source,
        "device_profile": book.device_profile,
        "is_original": book.is_original,
        "origin_book_id": book.origin_book_id,
        "missing": book.missing,
        "added_at": book.added_at.isoformat() if book.added_at else None,
        "updated_at": book.updated_at.isoformat() if book.updated_at else None,
        "has_cover": bool(book.cover_path),
    }


def book_detail(book: Book) -> dict:
    data = book_summary(book)
    data.update(
        {
            "description": book.description,
            "publisher": book.publisher,
            "isbn": book.isbn,
            "published": book.published,
            "reading_direction": book.reading_direction,
            "age_rating": book.age_rating,
            "file_path": book.file_path,
            "file_hash": book.file_hash,
        }
    )
    return data


def job(job: ConversionJob) -> dict:
    from app.web.labels import converter_label, profile_label, status_label

    output = getattr(job, "output_book", None)
    return {
        "id": job.id,
        "book_id": job.book_id,
        "book_title": job.book.title if job.book else None,
        "target_format": job.target_format,
        "target_format_label": job.target_format.upper(),
        "device_profile": job.device_profile,
        "device_profile_name": profile_label(job.device_profile),
        "converter": job.converter,
        "converter_name": converter_label(job.converter),
        "status": job.status,
        "status_label": status_label(job.status),
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "attempts": job.attempts,
        "keep_original": job.keep_original,
        "output_book_id": job.output_book_id,
        "output_book_title": output.title if output else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def feed(feed: Feed) -> dict:
    return {
        "id": feed.id,
        "name": feed.name,
        "url": feed.url,
        "category_id": feed.category_id,
        "interval_minutes": feed.interval_minutes,
        "active": feed.active,
        "output_format": feed.output_format,
        "device_profile": feed.device_profile,
        "destination_folder": feed.destination_folder,
        "keep_original": feed.keep_original,
        "max_items_per_run": feed.max_items_per_run,
        "last_checked_at": feed.last_checked_at.isoformat() if feed.last_checked_at else None,
        "last_error": feed.last_error,
        "consecutive_failures": feed.consecutive_failures,
    }


def feed_item(item: FeedItem) -> dict:
    return {
        "id": item.id,
        "guid": item.guid,
        "title": item.title,
        "url": item.url,
        "status": item.status,
        "error": item.error,
        "book_id": item.book_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
