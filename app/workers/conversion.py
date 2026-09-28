"""Executing one conversion job end-to-end."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import update

from app.converters.runner import run_conversion
from app.database import session_scope
from app.database.models import Book, ConversionJob, FeedItem, JobStatus
from app.library.detect import detect
from app.library.importer import link_conversion, stage_conversion_file
from app.library.service import delete_books
from app.storage.temp import temp_workdir
from app.workers import queue
from app.workers.context import JobContext, build_context
from app.workers.progress import JobCancelled, ProgressReporter

logger = logging.getLogger(__name__)


async def execute_job(job_id: str) -> None:
    context = _load_context(job_id)
    if context is None:
        return

    reporter = ProgressReporter(job_id)
    try:
        stored_path, converter, page_count, message = await _convert(context, reporter)
    except JobCancelled:
        # The user discarded this job while it was running: the conversion
        # aborted at a progress tick and nothing was published.
        logger.info("conversion cancelled", extra={"job_id": job_id})
        with session_scope() as session:
            queue.note_cancelled(
                session,
                job_id,
                "Cancelada — a conversão foi interrompida e o resultado descartado.",
            )
        return
    except Exception as exc:  # noqa: BLE001 - report any failure on the job
        logger.exception("conversion job failed", extra={"job_id": job_id})
        with session_scope() as session:
            queue.fail(session, job_id, f"{type(exc).__name__}: {exc}")
        return

    _finalize(context, stored_path, converter, page_count, message)


def _load_context(job_id: str) -> JobContext | None:
    with session_scope() as session:
        job = session.get(ConversionJob, job_id)
        if job is None or job.status != JobStatus.RUNNING.value:
            return None
        try:
            return build_context(session, job)
        except Exception as exc:  # noqa: BLE001
            queue.fail(session, job_id, str(exc))
            return None


async def _convert(
    context: JobContext, reporter: ProgressReporter
) -> tuple[str, str, int | None, str]:
    """Run the (blocking) conversion in a thread and stage its output."""
    with temp_workdir(f"conv_{context.job_id[:8]}_") as workdir:
        result, plan = await asyncio.to_thread(
            run_conversion,
            source=context.source_path,
            detection=context.detection,
            metadata=context.metadata,
            profile=context.profile,
            workdir=workdir,
            target_format=context.target_format,
            options=context.options,
            cover_path=context.cover_path,
            progress=reporter,
        )

        with session_scope() as session:
            queue.set_converter(session, context.job_id, result.converter)
            book = session.get(Book, context.book_id)
            stored_path = stage_conversion_file(
                session, book, result.output_path, context.profile.slug
            )

    message = result.message or f"Convertido para {plan.target_format.upper()}"
    return stored_path, result.converter, result.page_count, message


def _finalize(
    context: JobContext,
    stored_path: str,
    converter: str,
    page_count: int | None,
    message: str,
) -> None:
    with session_scope() as session:
        job = session.get(ConversionJob, context.job_id)
        if job is None or job.status == JobStatus.CANCELLED.value:
            # Cancelled between the progress ticks and the end: the produced
            # file must not stay behind as an orphan.
            logger.info("job was cancelled; discarding output", extra={"job_id": context.job_id})
            _discard_staged(stored_path)
            return

        book = session.get(Book, context.book_id)
        absolute = _absolute(stored_path)

        metadata = context.metadata
        if page_count:
            metadata.page_count = page_count

        converted = link_conversion(
            session,
            book,
            stored_path=stored_path,
            detection=detect(absolute),
            metadata=metadata,
            device_profile=context.profile.slug,
            keep_original=context.keep_original,
        )

        queue.finish(
            session,
            context.job_id,
            output_book_id=converted.id,
            output_path=stored_path,
            message=message,
        )

        if not context.keep_original and book.is_original:
            # Deleting the original cascades to its jobs and nulls the feed
            # items that point at it, which would erase the link between the
            # feed and the produced book. Re-point both at the converted book.
            session.execute(
                update(ConversionJob)
                .where(ConversionJob.id == context.job_id)
                .values(book_id=converted.id)
            )
            session.execute(
                update(FeedItem)
                .where(FeedItem.book_id == book.id)
                .values(book_id=converted.id)
            )
            session.commit()
            delete_books(session, [book], delete_files=True)
            logger.info(
                "original removed after conversion",
                extra={"book_id": context.book_id, "kept": converted.id},
            )


def _absolute(rel_path: str):
    from app.config import get_settings

    return get_settings().library_dir / rel_path


def _discard_staged(stored_path: str) -> None:
    """Delete a staged output file that will not become a book."""
    from app.config import get_settings

    try:
        library = get_settings().library_dir.resolve()
        target = _absolute(stored_path).resolve()
        if library in target.parents and target.is_file():
            target.unlink()
            logger.info("discarded staged file", extra={"path": stored_path})
    except OSError:  # a missing/locked file must not break the worker
        logger.warning("could not discard staged file", extra={"path": stored_path})
