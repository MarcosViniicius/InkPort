"""Web Reader routes: open a library file as a browser reader.

The page itself is a thin shell; everything else comes from
``/reader/{id}/manifest`` and the byte endpoints (page/chapter/asset/raw).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database.base import get_session
from app.devices.builtin import DEFAULT_PROFILE_SLUG
from app.devices.registry import all_profiles, get_profile
from app.library import repository
from app.library.detect import detect
from app.library.formats import media_type_for
from app.reader.base import ReaderContext, ReaderError
from app.reader.cache import ReaderCache
from app.reader.document import UnsupportedHandler
from app.reader.manifest import build_manifest, serialize_profiles
from app.reader.registry import select_handler
from app.security.auth import require_panel
from app.storage.paths import resolve_library_path
from app.web.templating import render

router = APIRouter(prefix="/reader", dependencies=[Depends(require_panel)], tags=["leitor"])

#: Keep a reference to in-flight preparation futures so they are not GC'd.
_tasks: set[asyncio.Future] = set()


@router.get("/{book_id}")
def reader_page(book_id: str, request: Request, session: Session = Depends(get_session)):
    book = repository.get_book(session, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Livro não encontrado")
    try:
        file_exists = resolve_library_path(book.file_path).exists()
    except ValueError:
        file_exists = False
    return render(
        request,
        "reader.html",
        {
            "active": "library",
            "book": book,
            "file_exists": file_exists,
        },
    )


@router.get("/{book_id}/manifest")
async def reader_manifest(book_id: str, session: Session = Depends(get_session)):
    book = await _book(session, book_id)
    path = _path(book)
    detection = await asyncio.to_thread(detect, path)
    ctx = _context(book, path, detection, session)
    handler = select_handler(book, detection) or UnsupportedHandler()

    if handler.needs_prepare(ctx) and handler.begin_prepare(ctx):
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(None, handler.prepare, ctx)
        _tasks.add(future)
        future.add_done_callback(_tasks.discard)

    try:
        manifest = await asyncio.to_thread(handler.manifest, ctx)
    except ReaderError as exc:
        manifest = build_manifest(
            ctx,
            handler=handler,
            capabilities=handler.capabilities(ctx),
            message=exc.message,
            state="unavailable" if exc.status_code == 415 else "error",
        )
    if handler.needs_prepare(ctx) and manifest.get("state") != "error":
        manifest["state"] = "preparing"
    return JSONResponse(manifest)


@router.get("/{book_id}/raw")
async def reader_raw(book_id: str, session: Session = Depends(get_session)):
    book = await _book(session, book_id)
    path = _path(book)
    detection = await asyncio.to_thread(detect, path)
    ctx = _context(book, path, detection, session)
    handler = select_handler(book, detection) or UnsupportedHandler()
    try:
        response = await asyncio.to_thread(handler.raw, ctx)
    except ReaderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    if response is not None:
        return response
    return FileResponse(
        path,
        media_type=media_type_for(detection.format, book.media_type),
        filename=f"{book.title}.{detection.format or book.format}",
        content_disposition_type="inline",
    )


@router.get("/{book_id}/page/{index}")
async def reader_page_image(
    book_id: str,
    index: int,
    w: int = Query(0, ge=0, le=8192),
    gray: bool = False,
    session: Session = Depends(get_session),
):
    book = await _book(session, book_id)
    path = _path(book)
    detection = await asyncio.to_thread(detect, path)
    ctx = _context(book, path, detection, session)
    handler = select_handler(book, detection) or UnsupportedHandler()
    try:
        return await asyncio.to_thread(handler.page, ctx, index, width=w, gray=gray)
    except ReaderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/{book_id}/chapter/{key:path}")
async def reader_chapter(
    book_id: str, key: str, session: Session = Depends(get_session)
):
    book = await _book(session, book_id)
    path = _path(book)
    detection = await asyncio.to_thread(detect, path)
    ctx = _context(book, path, detection, session)
    handler = select_handler(book, detection) or UnsupportedHandler()
    try:
        return await asyncio.to_thread(handler.chapter, ctx, key)
    except ReaderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/{book_id}/asset/{key:path}")
async def reader_asset(
    book_id: str, key: str, session: Session = Depends(get_session)
):
    book = await _book(session, book_id)
    path = _path(book)
    detection = await asyncio.to_thread(detect, path)
    ctx = _context(book, path, detection, session)
    handler = select_handler(book, detection) or UnsupportedHandler()
    try:
        return await asyncio.to_thread(handler.asset, ctx, key)
    except ReaderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


# --- helpers --------------------------------------------------------------
async def _book(session: Session, book_id: str):
    book = repository.get_book(session, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Livro não encontrado")
    return book


def _path(book) -> Path:
    try:
        path = resolve_library_path(book.file_path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Caminho fora da biblioteca") from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Arquivo ausente")
    return path


def _context(book, path: Path, detection, session: Session) -> ReaderContext:
    slug = book.device_profile or DEFAULT_PROFILE_SLUG
    profiles = all_profiles(session)
    profile = get_profile(slug, session)
    direction = "rtl" if (book.reading_direction or "").lower() == "rtl" else "ltr"
    if direction != "rtl" and getattr(profile, "manga_rtl", False):
        direction = "rtl"
    fmt = (detection.format or book.format or "").lower()
    return ReaderContext(
        book=book,
        path=path,
        detection=detection,
        format=fmt,
        direction=direction,
        profiles=serialize_profiles(profiles),
        profile_slug=slug,
        profile=profile,
        cache=ReaderCache(book.id, path),
    )


__all__ = ["router"]
