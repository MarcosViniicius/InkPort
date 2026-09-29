"""Web panel: RSS/Atom feeds."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.base import get_session
from app.database.models import Feed, FeedItem
from app.devices.registry import all_profiles
from app.rss.naming import feed_category_name
from app.rss.service import feeds_in_progress, preview_feed, process_feed_by_id
from app.security.auth import require_panel
from app.web.templating import render

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feeds", dependencies=[Depends(require_panel)], tags=["painel"])

#: Formatos que o formulário oferece — e os únicos que o salvamento aceita.
OUTPUT_FORMATS = ("epub", "cbz", "pdf", "mobi", "azw3", "kepub")

#: Períodos aceitos em "Buscar do passado" (0 = desligado, -1 = todo o acervo).
BACKFILL_MONTHS = (-1, 0, 6, 12, 24, 60)


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
            "profiles": all_profiles(session),
            "output_formats": list(OUTPUT_FORMATS),
            "running_feeds": feeds_in_progress(),
        },
    )


@router.get("/{feed_id}/edit")
def edit_feed_page(feed_id: int, request: Request, session: Session = Depends(get_session)):
    """Editar um feed: o mesmo formulário da criação, já preenchido."""
    feed = session.get(Feed, feed_id)
    if feed is None:
        return _back("/feeds", "Feed não encontrado.")
    itens = session.scalar(
        select(func.count(FeedItem.id)).where(FeedItem.feed_id == feed.id)
    )
    livros = session.scalar(
        select(func.count(FeedItem.id)).where(
            FeedItem.feed_id == feed.id, FeedItem.book_id.is_not(None)
        )
    )
    return render(
        request,
        "feed_edit.html",
        {
            "active": "feeds",
            "feed": feed,
            "destination": feed_category_name(feed),
            "items_count": int(itens or 0),
            "books_count": int(livros or 0),
            "profiles": all_profiles(session),
            "output_formats": list(OUTPUT_FORMATS),
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
async def save_feed(
    feed_id: str = Form(""),
    name: str = Form(""),
    url: str = Form(""),
    category_id: str = Form(""),
    interval_minutes: str = Form(""),
    output_format: str = Form("epub"),
    device_profile: str = Form("generic_epub"),
    destination_folder: str = Form(""),
    max_items_per_run: str = Form(""),
    backfill_months: str = Form("0"),
    sitemap_url: str = Form(""),
    active: str = Form(""),
    keep_original: str = Form(""),
    session: Session = Depends(get_session),
):
    """Cria ou atualiza um feed — o mesmo formulário serve para os dois casos.

    Os números chegam como texto de propósito: campo vazio ou fora do padrão vira
    o valor anterior (ou o padrão), em vez de um 422 na cara do usuário.
    """
    feed: Feed | None = None
    if feed_id.strip():
        identificador = _as_int(feed_id, default=None)
        feed = session.get(Feed, identificador) if identificador is not None else None
        if feed is None:
            return _back("/feeds", "Feed não encontrado.")
        destino = f"/feeds/{feed.id}/edit"
    else:
        # ``novo=1`` reabre a seção do formulário quando o salvamento falha.
        destino = "/feeds?novo=1"

    nome = name.strip()
    endereco = url.strip()
    if not nome or not endereco:
        return _back(destino, "Informe o nome e a URL do feed.")

    # A URL é única no banco: avise antes de o commit estourar um IntegrityError.
    repetido = session.scalar(select(Feed).where(Feed.url == endereco))
    if repetido is not None and (feed is None or repetido.id != feed.id):
        return _back(destino, f"Já existe um feed com esta URL: «{repetido.name}».")

    if feed is None:
        feed = Feed(name=nome, url=endereco)
        session.add(feed)

    feed.name = nome
    feed.url = endereco
    feed.interval_minutes = _as_int(
        interval_minutes, default=feed.interval_minutes or 360, minimum=5
    )
    feed.output_format = _output_format(output_format, default=feed.output_format or "epub")
    feed.device_profile = _device_profile(
        device_profile, session, default=feed.device_profile or "generic_epub"
    )
    feed.destination_folder = destination_folder.strip()
    feed.max_items_per_run = _as_int(
        max_items_per_run, default=feed.max_items_per_run or 20, minimum=1
    )
    # Retroativos: mudar o período invalida o "arquivo já percorrido", então a
    # próxima busca volta a varrer o sitemap.
    anterior = feed.backfill_months or 0
    feed.backfill_months = _backfill_months(backfill_months, default=anterior)
    feed.sitemap_url = _sitemap_url(sitemap_url)
    if feed.backfill_months != anterior:
        feed.backfill_done_at = None
    feed.active = bool(active)
    feed.keep_original = bool(keep_original)
    # Só mexe se o formulário trouxe algo: a API pode ter definido uma categoria.
    if category_id.strip():
        feed.category_id = _as_int(category_id, default=None)

    session.commit()

    # "Automático ao salvar": começa a puxar o acervo antigo em segundo plano.
    if feed.backfill_months and feed.backfill_done_at is None:
        asyncio.create_task(asyncio.to_thread(_backfill_in_background, feed.id))

    verb = "atualizado" if feed_id.strip() else "criado"
    extra = (
        " Buscando os posts antigos em segundo plano."
        if feed.backfill_months and feed.backfill_done_at is None
        else ""
    )
    return RedirectResponse(
        f"/feeds?ok={quote(f'Feed {verb}: {feed.name}.{extra}')}", status_code=303
    )


def _back(destino: str, mensagem: str) -> RedirectResponse:
    """Volta para a tela anterior com um aviso legível (nunca um 500)."""
    separador = "&" if "?" in destino else "?"
    return RedirectResponse(f"{destino}{separador}err={quote(mensagem)}", status_code=303)


def _as_int(raw: str, *, default: int | None, minimum: int | None = None) -> int | None:
    """Int tolerante: texto vazio ou inválido mantém ``default``."""
    try:
        valor = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, valor) if minimum is not None else valor


def _output_format(raw: str, *, default: str) -> str:
    valor = (raw or "").strip().lower()
    return valor if valor in OUTPUT_FORMATS else default


def _device_profile(raw: str, session: Session, *, default: str) -> str:
    """Perfil que existe nesta instalação; qualquer outra coisa mantém o atual."""
    slug = (raw or "").strip()
    return slug if slug in all_profiles(session) else default


def _backfill_months(raw: str, *, default: int) -> int:
    """Só aceita os presets oferecidos; qualquer outra coisa mantém o atual."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value in BACKFILL_MONTHS else default


def _sitemap_url(raw: str) -> str:
    """Empty, or a real http(s) URL (anything else is ignored)."""
    value = (raw or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""


@router.post("/{feed_id}/delete")
def delete_feed(feed_id: int, session: Session = Depends(get_session)):
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


@router.post("/{feed_id}/backfill")
async def backfill_feed(feed_id: int, session: Session = Depends(get_session)):
    """Force another walk of the site archive in the background."""
    feed = session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed não encontrado")
    if not feed.backfill_months:
        return _back(
            "/feeds",
            "Ligue «Buscar do passado» no feed e salve para importar os posts antigos.",
        )
    if feed_id in feeds_in_progress():
        return _back("/feeds", "Este feed já está sendo processado agora.")
    asyncio.create_task(asyncio.to_thread(_backfill_in_background, feed_id))
    return RedirectResponse(
        f"/feeds?ok={quote('Busca retroativa iniciada — os posts antigos entram conforme ficam prontos.')}",
        status_code=303,
    )


def _backfill_in_background(feed_id: int) -> None:
    """Clear the "archive finished" mark and run the normal (RSS + archive) pass."""
    from app.database import session_scope

    if feed_id in feeds_in_progress():
        return
    try:
        with session_scope() as session:
            feed = session.get(Feed, feed_id)
            if feed is None or not feed.backfill_months:
                return
            feed.backfill_done_at = None
            session.commit()
        report = process_feed_by_id(feed_id)
    except Exception:  # noqa: BLE001 - background task: never crash the loop
        logger.exception("background feed backfill failed", extra={"feed_id": feed_id})
        return
    if report is not None:
        logger.info("manual feed backfill finished", extra=report.as_dict())
