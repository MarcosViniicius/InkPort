"""Web panel: RSS/Atom feeds."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.base import get_session
from app.database.models import Feed, FeedItem
from app.devices.registry import all_profiles
from app.library import repository
from app.rss.service import feeds_in_progress, preview_feed, process_feed_by_id
from app.security.auth import require_panel
from app.web.templating import render

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feeds", dependencies=[Depends(require_panel)], tags=["painel"])


@router.get("")
def feeds_page(request: Request, session: Session = Depends(get_session)):
    feeds = session.scalars(select(Feed).order_by(Feed.name)).all()
    recent_items = session.scalars(
        select(FeedItem).order_by(FeedItem.created_at.desc()).limit(30)
    ).all()
    return render(
        request,
        "feeds.html",
        {
            "active": "feeds",
            "feeds": feeds,
            "recent_items": recent_items,
            "categories": repository.categories(session),
            "profiles": all_profiles(session),
            "output_formats": ["epub", "cbz", "pdf", "mobi", "azw3", "kepub"],
            "running_feeds": feeds_in_progress(),
        },
    )


@router.post("/test")
def test_feed(url: str = Form("")):
    """Dry run: fetch and parse a feed without importing anything."""
    if not url.strip():
        return JSONResponse({"ok": False, "error": "Informe a URL do feed."})
    preview = preview_feed(url.strip())
    return JSONResponse(preview.as_dict())


@router.post("/save")
def save_feed(
    feed_id: str = Form(""),
    name: str = Form(...),
    url: str = Form(...),
    category_id: str = Form(""),
    interval_minutes: int = Form(360),
    output_format: str = Form("epub"),
    device_profile: str = Form("generic_epub"),
    destination_folder: str = Form(""),
    max_items_per_run: int = Form(20),
    active: str = Form(""),
    keep_original: str = Form(""),
    session: Session = Depends(get_session),
):
    if feed_id.strip():
        feed = session.get(Feed, int(feed_id))
        if feed is None:
            raise HTTPException(status_code=404, detail="Feed não encontrado")
    else:
        feed = Feed(url=url.strip())
        session.add(feed)

    feed.name = name.strip()
    feed.url = url.strip()
    feed.category_id = int(category_id) if category_id.strip() else None
    feed.interval_minutes = max(5, int(interval_minutes))
    feed.output_format = output_format.strip()
    feed.device_profile = device_profile.strip()
    feed.destination_folder = destination_folder.strip()
    feed.max_items_per_run = max(1, int(max_items_per_run))
    feed.active = bool(active)
    feed.keep_original = bool(keep_original)
    session.commit()
    from urllib.parse import quote

    verb = "atualizado" if feed_id.strip() else "criado"
    return RedirectResponse(
        f"/feeds?ok={quote(f'Feed {verb}: {feed.name}.')}", status_code=303
    )


@router.post("/{feed_id}/delete")
def delete_feed(feed_id: int, session: Session = Depends(get_session)):
    from urllib.parse import quote

    feed = session.get(Feed, feed_id)
    if feed is None:
        return RedirectResponse(
            f"/feeds?err={quote('Feed não encontrado.')}", status_code=303
        )
    name = feed.name
    session.delete(feed)
    session.commit()
    return RedirectResponse(
        f"/feeds?ok={quote(f'Feed removido: {name}. Os livros importados continuam na biblioteca.')}",
        status_code=303,
    )


@router.post("/{feed_id}/rebuild")
async def rebuild_feed(feed_id: int, session: Session = Depends(get_session)):
    """Delete what this feed imported and import everything again.

    Runs in the background: it deletes books, downloads every item and queues
    the conversions, which is far too slow to keep an HTTP request open. The
    handler must stay ``async``: ``asyncio.create_task`` needs a running loop,
    and FastAPI runs a plain ``def`` handler in a worker thread without one.
    """
    from urllib.parse import quote

    feed = session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed não encontrado")
    if feed_id in feeds_in_progress():
        return RedirectResponse(
            f"/feeds?err={quote('Este feed já está sendo processado agora.')}",
            status_code=303,
        )
    asyncio.create_task(asyncio.to_thread(_rebuild_in_background, feed_id))
    return RedirectResponse(
        f"/feeds?ok={quote('Refazendo o feed em segundo plano — os livros antigos serão substituídos.')}",
        status_code=303,
    )


def _rebuild_in_background(feed_id: int) -> None:
    try:
        _reset(feed_id, True)
        report = process_feed_by_id(feed_id)
        if report is not None:
            logger.info("feed rebuilt", extra=report.as_dict())
    except Exception:  # noqa: BLE001 - background task: never crash the loop
        logger.exception("feed rebuild failed", extra={"feed_id": feed_id})


def _reset(feed_id: int, remove_books: bool) -> None:
    from app.database import session_scope
    from app.rss.service import reset_feed

    with session_scope() as inner:
        feed = inner.get(Feed, feed_id)
        if feed is not None:
            reset_feed(inner, feed, remove_books=remove_books)


@router.post("/{feed_id}/reset")
def reset_feed_items(feed_id: int, session: Session = Depends(get_session)):
    """Forget processed items only (keeps the imported books)."""
    from urllib.parse import quote

    from app.rss.service import reset_feed

    feed = session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed não encontrado")
    result = reset_feed(session, feed, remove_books=False)
    message = f"Histórico limpo: {result['items']} item(ns). Agora use Buscar agora."
    return RedirectResponse(f"/feeds?ok={quote(message)}", status_code=303)


@router.post("/{feed_id}/refresh")
async def refresh_feed(feed_id: int, session: Session = Depends(get_session)):
    """Start a search in the background and return immediately.

    Fetching a feed means downloading every new item, which can take minutes.
    Blocking the request would make the panel look frozen, so we hand it to a
    worker thread and tell the user where to watch the progress.
    """
    from urllib.parse import quote

    feed = session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed não encontrado")
    if feed_id in feeds_in_progress():
        return RedirectResponse(
            f"/feeds?err={quote('Este feed já está sendo buscado agora.')}",
            status_code=303,
        )
    asyncio.create_task(asyncio.to_thread(_refresh_in_background, feed_id))
    return RedirectResponse(
        f"/feeds?ok={quote('Busca iniciada — os itens aparecem em “Últimos itens” conforme chegam.')}",
        status_code=303,
    )


def _refresh_in_background(feed_id: int) -> None:
    try:
        report = process_feed_by_id(feed_id)
    except Exception:  # noqa: BLE001 - background task: never crash the loop
        logger.exception("background feed refresh failed", extra={"feed_id": feed_id})
        return
    if report is not None:
        logger.info("manual feed refresh finished", extra=report.as_dict())
