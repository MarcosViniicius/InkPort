"""REST API for RSS/Atom feeds."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api import serializers
from app.api.deps import require_api
from app.api.schemas import FeedPayload
from app.database.base import get_session
from app.database.models import Feed, FeedItem
from app.rss.service import process_feed_by_id

router = APIRouter(prefix="/api/feeds", tags=["API: RSS"], dependencies=[Depends(require_api)])


def _get_or_404(session: Session, feed_id: int) -> Feed:
    feed = session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed não encontrado")
    return feed


@router.get("")
def list_feeds(session: Session = Depends(get_session)) -> dict:
    feeds = session.scalars(select(Feed).order_by(Feed.name)).all()
    return {"items": [serializers.feed(feed) for feed in feeds]}


@router.post("")
def create_feed(payload: FeedPayload, session: Session = Depends(get_session)) -> dict:
    existing = session.scalar(select(Feed).where(Feed.url == payload.url))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Já existe um feed com esta URL")
    feed = Feed(**payload.model_dump())
    session.add(feed)
    session.commit()
    return serializers.feed(feed)


@router.patch("/{feed_id}")
def update_feed(
    feed_id: int, payload: FeedPayload, session: Session = Depends(get_session)
) -> dict:
    feed = _get_or_404(session, feed_id)
    for key, value in payload.model_dump().items():
        setattr(feed, key, value)
    session.commit()
    return serializers.feed(feed)


@router.delete("/{feed_id}")
def delete_feed(feed_id: int, session: Session = Depends(get_session)) -> dict:
    feed = _get_or_404(session, feed_id)
    session.delete(feed)
    session.commit()
    return {"ok": True}


@router.post("/{feed_id}/refresh")
async def refresh_feed(feed_id: int, session: Session = Depends(get_session)) -> dict:
    feed = _get_or_404(session, feed_id)
    # Its own session inside the worker thread (Session is not thread-safe).
    report = await asyncio.to_thread(process_feed_by_id, feed.id)
    if report is None:
        raise HTTPException(status_code=404, detail="Feed não encontrado")
    return {
        "feed_id": report.feed_id,
        "fetched": report.fetched,
        "downloaded": report.downloaded,
        "imported": report.imported,
        "queued": report.queued,
        "skipped": report.skipped,
        "articles": report.articles,
        "errors": report.errors,
    }


def _backfill_preview(feed_id: int) -> dict | None:
    from app.database import session_scope
    from app.rss.backfill import preview_backfill
    from app.rss.downloader import Downloader

    with session_scope() as session:
        feed = session.get(Feed, feed_id)
        if feed is None:
            return None
        with Downloader() as downloader:
            return preview_backfill(session, feed, downloader)


@router.get("/{feed_id}/backfill/preview")
async def backfill_preview(feed_id: int, session: Session = Depends(get_session)) -> dict:
    """Dry run of the retroactive pull: counts and a sample, downloads nothing."""
    _get_or_404(session, feed_id)
    preview = await asyncio.to_thread(_backfill_preview, feed_id)
    if preview is None:
        raise HTTPException(status_code=404, detail="Feed não encontrado")
    return preview


@router.get("/{feed_id}/items")
def list_items(
    feed_id: int,
    limit: int = 100,
    session: Session = Depends(get_session),
) -> dict:
    _get_or_404(session, feed_id)
    items = session.scalars(
        select(FeedItem)
        .where(FeedItem.feed_id == feed_id)
        .order_by(FeedItem.created_at.desc())
        .limit(limit)
    ).all()
    return {"items": [serializers.feed_item(item) for item in items]}


@router.post("/{feed_id}/reset")
def reset_items(feed_id: int, session: Session = Depends(get_session)) -> dict:
    """Forget the processed items so the next run imports them again.

    Useful after the converter improves: press this and "refresh" to rebuild the
    articles with the current pipeline.
    """
    _get_or_404(session, feed_id)
    removed = session.execute(
        delete(FeedItem).where(FeedItem.feed_id == feed_id)
    ).rowcount
    feed = session.get(Feed, feed_id)
    if feed is not None:
        feed.last_checked_at = None
    session.commit()
    return {"removed": removed}
