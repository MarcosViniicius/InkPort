"""OPDS 1.2 single-book entries and binary assets (cover, download)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.base import get_session
from app.database.models import Book
from app.downloads import store as downloads
from app.downloads.response import TrackedFileResponse
from app.library.formats import media_type_for
from app.metadata.cover import get_thumbnail
from app.opds import entries, feeds, queries, urls
from app.opds.constants import ACQUISITION_TYPE
from app.opds.v1._common import xml_response
from app.storage.paths import resolve_cover_path, resolve_library_path

router = APIRouter()


@router.get("/books/{book_id}")
def opds_book(book_id: str, session: Session = Depends(get_session)) -> Response:
    book = session.get(Book, book_id)
    if book is None or not queries.is_offerable(session, book_id):
        raise HTTPException(status_code=404, detail="Livro não encontrado")
    root = feeds.build_feed(
        title=book.title, feed_id=urls.book_entry(book.id), self_href=urls.book_entry(book.id)
    )
    feeds.add_start_link(root, urls.root())
    entries.book_entry(root, book, [book])
    return xml_response(root, ACQUISITION_TYPE)


@router.get("/cover/{book_id}")
def opds_cover(book_id: str, session: Session = Depends(get_session)) -> FileResponse:
    return FileResponse(_cover_file(session, book_id, thumbnail=False), media_type="image/jpeg")


@router.get("/thumbnail/{book_id}")
def opds_thumbnail(book_id: str, session: Session = Depends(get_session)) -> FileResponse:
    return FileResponse(_cover_file(session, book_id, thumbnail=True), media_type="image/jpeg")


@router.get("/download/{book_id}")
def opds_download(
    book_id: str, request: Request, session: Session = Depends(get_session)
) -> FileResponse:
    book = session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Livro não encontrado")
    if not queries.is_offerable(session, book_id):
        raise HTTPException(status_code=404, detail="Arquivo não disponível")
    try:
        path = resolve_library_path(book.file_path)
    except ValueError:
        raise HTTPException(status_code=404, detail="Caminho inválido") from None
    if not path.exists():
        raise HTTPException(status_code=404, detail="Arquivo ausente no servidor")

    media_type = media_type_for(book.format, book.media_type)
    filename = f"{book.title}.{book.format}".replace("/", "-")
    # Start the tracking here (request session) and close it in the response, so
    # "access to the feed" stays different from "bytes actually delivered".
    track = downloads.start_download(
        session,
        book,
        user_agent=request.headers.get("user-agent"),
        range_header=request.headers.get("range"),
    )
    return TrackedFileResponse(
        path,
        track_id=track.id,
        countable=track.countable,
        total_bytes=path.stat().st_size or track.total_bytes,
        media_type=media_type,
        filename=filename,
    )


def _cover_file(session: Session, book_id: str, *, thumbnail: bool):
    book = session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Livro não encontrado")
    cover = resolve_cover_path(book.cover_path)
    if cover is None:
        raise HTTPException(status_code=404, detail="Capa não disponível")
    if not thumbnail:
        return cover
    thumb = get_thumbnail(cover.name, get_settings().covers_dir)
    return thumb or cover
