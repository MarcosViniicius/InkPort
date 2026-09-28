"""REST API for conversions and the job queue."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import serializers
from app.api.deps import require_api
from app.api.schemas import ConversionCreate
from app.converters.planner import describe, plan_conversion
from app.database.base import get_session
from app.database.models import Book
from app.devices.registry import get_profile
from app.library.conversions import compatible_targets, enqueue_conversion
from app.library.detect import detect
from app.metadata.extractor import extract_metadata
from app.storage.paths import resolve_library_path
from app.workers import queue

router = APIRouter(
    prefix="/api/conversions", tags=["API: conversões"], dependencies=[Depends(require_api)]
)


@router.get("/targets")
def conversion_targets(
    book_id: str,
    device_profile: str = "generic_epub",
    session: Session = Depends(get_session),
) -> dict:
    book = session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Livro não encontrado")

    path = resolve_library_path(book.file_path)
    detection = detect(path)
    metadata = extract_metadata(path, detection)
    for field in ("title", "author", "series", "series_index", "language", "reading_direction"):
        value = getattr(book, field, None)
        if value:
            setattr(metadata, field, value)

    profile = get_profile(device_profile, session)
    plan = plan_conversion(detection, metadata, profile)
    return {
        "detection": {
            "format": detection.format,
            "content_type": detection.content_type,
            "image_only": detection.image_only,
            "page_count": detection.page_count,
            "note": detection.note,
        },
        "device_profile": profile.slug,
        "plan": {"target_format": plan.target_format, "reason": plan.reason, "description": describe(plan)},
        "targets": compatible_targets(session, book, device_profile),
    }


@router.post("")
def create_conversions(
    payload: ConversionCreate, session: Session = Depends(get_session)
) -> dict:
    jobs = []
    for book_id in payload.book_ids:
        book = session.get(Book, book_id)
        if book is None:
            continue
        jobs.append(
            enqueue_conversion(
                session,
                book,
                target_format=payload.target_format,
                device_profile=payload.device_profile,
                keep_original=payload.keep_original,
                options=payload.options,
            )
        )
    return {"created": len(jobs), "jobs": [serializers.job(job) for job in jobs]}


@router.get("")
def list_jobs(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict:
    jobs = queue.list_jobs(session, status=status, limit=limit)
    return {
        "items": [serializers.job(job) for job in jobs],
        "counts": queue.counts_by_status(session),
    }


@router.get("/stats")
def job_stats(session: Session = Depends(get_session)) -> dict:
    return {
        "counts": queue.counts_by_status(session),
        "pending": queue.pending_count(session),
        "running": queue.active_count(session),
    }


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, session: Session = Depends(get_session)) -> dict:
    if not queue.cancel(session, job_id):
        raise HTTPException(status_code=409, detail="Job não pode ser cancelado")
    return {"ok": True}


@router.post("/{job_id}/retry")
def retry_job(job_id: str, session: Session = Depends(get_session)) -> dict:
    if not queue.retry(session, job_id):
        raise HTTPException(status_code=409, detail="Job não pode ser reenfileirado")
    return {"ok": True}
