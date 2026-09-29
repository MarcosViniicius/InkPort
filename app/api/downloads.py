"""REST API for download tracking and the file lifecycle.

This is the hook the future file manager and auto-cleanup will use: the tracking
itself happens in the delivery path, these endpoints only read and change the
per-file state.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_api
from app.database.base import get_session
from app.database.models import Book, FileRecord, FileState
from app.downloads import store as downloads

router = APIRouter(prefix="/api/downloads", tags=["API: downloads"], dependencies=[Depends(require_api)])


def _book_or_404(session: Session, book_id: str) -> Book:
    book = session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Livro não encontrado")
    return book


@router.get("")
def list_downloads(
    state: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> dict:
    statement = (
        select(FileRecord)
        .order_by(FileRecord.last_download_at.desc().nullslast(), FileRecord.created_at.desc())
        .limit(limit)
    )
    if state:
        statement = statement.where(FileRecord.state == state)
    return {"items": [downloads.summary(row) for row in session.scalars(statement).all()]}


@router.get("/book/{book_id}")
def get_for_book(book_id: str, session: Session = Depends(get_session)) -> dict:
    _book_or_404(session, book_id)
    return downloads.stats(session, book_id) or {
        "book_id": book_id,
        "state": FileState.AVAILABLE.value,
        "download_count": 0,
        "active_downloads": 0,
    }


@router.post("/book/{book_id}/downloaded")
def mark_downloaded(book_id: str, session: Session = Depends(get_session)) -> dict:
    downloads.ensure_record(session, _book_or_404(session, book_id))
    session.commit()
    downloads.mark_downloaded(session, book_id)
    return downloads.summary(downloads.get_record(session, book_id)) or {}


@router.post("/book/{book_id}/block")
def block(book_id: str, session: Session = Depends(get_session)) -> dict:
    """Mark as 'do not download again' (not hidden from the feeds yet)."""
    downloads.ensure_record(session, _book_or_404(session, book_id))
    session.commit()
    downloads.block(session, book_id)
    return downloads.summary(downloads.get_record(session, book_id)) or {}


@router.post("/book/{book_id}/unblock")
def unblock(book_id: str, session: Session = Depends(get_session)) -> dict:
    downloads.ensure_record(session, _book_or_404(session, book_id))
    session.commit()
    downloads.unblock(session, book_id)
    return downloads.summary(downloads.get_record(session, book_id)) or {}
