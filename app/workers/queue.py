"""Persistent conversion queue: enqueue, claim, update, requeue.

The queue *is* the ``conversion_jobs`` table. Claiming is done with a single
UPDATE ... WHERE status='pending' so a second worker cannot grab the same row,
and stale RUNNING jobs are requeued on startup so interrupted work resumes.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.database.models import Book, ConversionJob, JobStatus, utcnow


def enqueue(
    session: Session,
    book: Book,
    *,
    target_format: str,
    device_profile: str,
    options: dict | None = None,
    keep_original: bool = True,
) -> ConversionJob:
    job = ConversionJob(
        book_id=book.id,
        target_format=target_format,
        device_profile=device_profile,
        options=options or {},
        keep_original=keep_original,
        status=JobStatus.PENDING.value,
    )
    session.add(job)
    session.commit()
    return job


def claim_next(session: Session) -> ConversionJob | None:
    """Atomically move one pending job to RUNNING and return it."""
    candidate = session.scalar(
        select(ConversionJob)
        .where(ConversionJob.status == JobStatus.PENDING.value)
        .order_by(ConversionJob.created_at.asc())
        .limit(1)
    )
    if candidate is None:
        return None

    result = session.execute(
        update(ConversionJob)
        .where(
            ConversionJob.id == candidate.id,
            ConversionJob.status == JobStatus.PENDING.value,
        )
        .values(
            status=JobStatus.RUNNING.value,
            started_at=utcnow(),
            heartbeat_at=utcnow(),
            attempts=ConversionJob.attempts + 1,
            progress=0,
            message="Iniciando",
        )
    )
    session.commit()
    if result.rowcount == 0:
        return None  # another worker claimed it first
    return session.get(ConversionJob, candidate.id)


def set_progress(session: Session, job_id: str, progress: int, message: str = "") -> None:
    session.execute(
        update(ConversionJob)
        .where(ConversionJob.id == job_id)
        .values(progress=progress, message=message or None, heartbeat_at=utcnow())
    )
    session.commit()


def set_converter(session: Session, job_id: str, converter: str) -> None:
    session.execute(
        update(ConversionJob).where(ConversionJob.id == job_id).values(converter=converter)
    )
    session.commit()


def finish(
    session: Session,
    job_id: str,
    *,
    output_book_id: str | None,
    output_path: str | None,
    message: str,
) -> None:
    session.execute(
        update(ConversionJob)
        .where(ConversionJob.id == job_id)
        .values(
            status=JobStatus.DONE.value,
            progress=100,
            message=message,
            output_book_id=output_book_id,
            output_path=output_path,
            finished_at=utcnow(),
            heartbeat_at=utcnow(),
        )
    )
    session.commit()


def fail(session: Session, job_id: str, error: str) -> None:
    session.execute(
        update(ConversionJob)
        .where(ConversionJob.id == job_id)
        .values(
            status=JobStatus.FAILED.value,
            error=error[:4000],
            finished_at=utcnow(),
            heartbeat_at=utcnow(),
        )
    )
    session.commit()


def cancel(session: Session, job_id: str) -> bool:
    """Ask for a job to be discarded.

    A pending job never runs. A running one is marked CANCELLED and the worker
    notices on its next progress tick -- it aborts the conversion and throws the
    partial result away (see ``app/workers/progress.py``).
    """
    job = session.get(ConversionJob, job_id)
    if job is None:
        return False
    if job.status == JobStatus.PENDING.value:
        job.status = JobStatus.CANCELLED.value
        job.message = "Cancelada antes de começar."
    elif job.status == JobStatus.RUNNING.value:
        job.status = JobStatus.CANCELLED.value
        job.message = "Cancelada — interrompendo a conversão em curso…"
    else:
        return False
    job.finished_at = utcnow()
    session.commit()
    return True


def is_cancelled(session: Session, job_id: str) -> bool:
    """True when the job was cancelled while it was running."""
    status = session.scalar(
        select(ConversionJob.status).where(ConversionJob.id == job_id)
    )
    return status == JobStatus.CANCELLED.value


def note_cancelled(session: Session, job_id: str, message: str) -> None:
    """Record how a cancelled job ended (it is already CANCELLED)."""
    session.execute(
        update(ConversionJob)
        .where(ConversionJob.id == job_id, ConversionJob.status == JobStatus.CANCELLED.value)
        .values(message=message, progress=0, finished_at=utcnow())
    )
    session.commit()


def clear_finished(session: Session, *, statuses: tuple[str, ...] = ()) -> int:
    """Remove finished job records (never touches files)."""
    wanted = statuses or (
        JobStatus.DONE.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELLED.value,
    )
    removed = session.execute(
        delete(ConversionJob).where(ConversionJob.status.in_(wanted))
    ).rowcount
    session.commit()
    return removed


def retry(session: Session, job_id: str) -> bool:
    result = session.execute(
        update(ConversionJob)
        .where(
            ConversionJob.id == job_id,
            ConversionJob.status.in_(
                [JobStatus.FAILED.value, JobStatus.CANCELLED.value]
            ),
        )
        .values(
            status=JobStatus.PENDING.value,
            progress=0,
            error=None,
            message="Reagendado",
        )
    )
    session.commit()
    return result.rowcount > 0


def requeue_stale(session: Session, *, older_than_seconds: int = 120) -> int:
    """Return RUNNING jobs heartbeats to the queue (crash/interrupt recovery)."""
    cutoff = utcnow() - timedelta(seconds=older_than_seconds)
    result = session.execute(
        update(ConversionJob)
        .where(
            ConversionJob.status == JobStatus.RUNNING.value,
            (ConversionJob.heartbeat_at.is_(None))
            | (ConversionJob.heartbeat_at < cutoff),
        )
        .values(
            status=JobStatus.PENDING.value,
            progress=0,
            message="Retomado após interrupção",
        )
    )
    session.commit()
    return result.rowcount


def pending_count(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count(ConversionJob.id)).where(
                ConversionJob.status == JobStatus.PENDING.value
            )
        )
        or 0
    )


def active_count(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count(ConversionJob.id)).where(
                ConversionJob.status == JobStatus.RUNNING.value
            )
        )
        or 0
    )


def list_jobs(
    session: Session, *, status: str | None = None, limit: int = 50
) -> list[ConversionJob]:
    stmt = select(ConversionJob).order_by(ConversionJob.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(ConversionJob.status == status)
    return list(session.scalars(stmt).all())


def counts_by_status(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(ConversionJob.status, func.count(ConversionJob.id)).group_by(
            ConversionJob.status
        )
    ).all()
    base = {status.value: 0 for status in JobStatus}
    base.update({row[0]: row[1] for row in rows})
    return base
