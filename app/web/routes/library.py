"""Web panel: library browsing and book management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.serializers import book_detail
from app.converters.catalog import targets_for
from app.converters.planner import describe, plan_conversion
from app.database.base import get_session
from app.database.models import Book
from app.devices.registry import all_profiles, get_profile
from app.library import repository, service
from app.library.detect import detect
from app.metadata.extractor import extract_metadata
from app.opds import queries as opds_queries
from app.security.auth import require_panel
from app.storage.paths import resolve_cover_path, resolve_library_path
from app.web.templating import render

router = APIRouter(prefix="/library", dependencies=[Depends(require_panel)], tags=["painel"])


@router.get("")
def library(
    request: Request,
    q: str | None = None,
    # Kept as text on purpose: the filter form submits the field empty ("Todas"),
    # and an empty string is not a valid int -- that used to answer the panel with
    # a raw 422 JSON instead of the library page.
    category_id: str | None = None,
    author: str | None = None,
    series: str | None = None,
    tag: str | None = None,
    content_type: str | None = None,
    fmt: str | None = None,
    sort: str = "added_desc",
    page: str | None = None,
    session: Session = Depends(get_session),
):
    category = _as_int(category_id)
    current_page = _as_int(page) or 1
    result = repository.search(
        session,
        repository.BookQuery(
            q=q, category_id=category, author=author, series=series, tag=tag,
            content_type=content_type, fmt=fmt, sort=sort, page=current_page, per_page=24,
        ),
    )
    return render(
        request,
        "library.html",
        {
            "active": "library",
            "page_data": result,
            "filters": {
                "q": q or "", "category_id": category, "author": author,
                "series": series, "tag": tag, "content_type": content_type,
                "fmt": fmt, "sort": sort,
            },
            "categories": repository.categories(session),
            "authors": repository.authors(session, limit=100),
            "series_list": repository.series(session, limit=100),
            "tag_list": repository.tags(session, limit=100),
            "formats": repository.formats(session),
        },
    )


def _as_int(value: str | None) -> int | None:
    """Coerce a filter field: empty or junk means "no filter"."""
    text = (value or "").strip()
    if text.lstrip("-").isdigit():
        return int(text)
    return None


@router.get("/{book_id}")
def book_page(book_id: str, request: Request, session: Session = Depends(get_session)):
    book = repository.get_book(session, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Livro não encontrado")

    path = resolve_library_path(book.file_path)
    detection = detect(path)
    metadata = extract_metadata(path, detection)
    for field in ("title", "author", "series", "series_index", "language", "reading_direction", "description"):
        value = getattr(book, field, None)
        if value:
            setattr(metadata, field, value)

    profiles = all_profiles(session)
    device_profile = book.device_profile or "generic_epub"
    profile = get_profile(device_profile, session)
    plan = plan_conversion(detection, metadata, profile)

    return render(
        request,
        "book_detail.html",
        {
            "active": "library",
            "book": book,
            "data": book_detail(book),
            "detection": detection,
            "plan": describe(plan),
            "targets": targets_for(detection, profile),
            "profiles": list(profiles.values()),
            "device_profile": device_profile,
            "categories": repository.categories(session),
            "variants": opds_queries.variants(session, book),
            "origin": session.get(Book, book.origin_book_id) if book.origin_book_id else None,
            "file_exists": path.exists(),
            "file_size": path.stat().st_size if path.exists() else book.file_size,
        },
    )


@router.post("/{book_id}/edit")
def edit_book(
    book_id: str,
    title: str = Form(...),
    author: str = Form(""),
    series: str = Form(""),
    series_index: str = Form(""),
    publisher: str = Form(""),
    language: str = Form(""),
    published: str = Form(""),
    isbn: str = Form(""),
    description: str = Form(""),
    tags: str = Form(""),
    category: str = Form(""),
    reading_direction: str = Form("ltr"),
    rename_file: str = Form(""),
    session: Session = Depends(get_session),
):
    book = repository.get_book(session, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Livro não encontrado")

    index = _to_float(series_index)
    service.update_metadata(
        session,
        book,
        {
            "title": title.strip(),
            "author": author.strip() or None,
            "series": series.strip() or None,
            "series_index": index,
            "publisher": publisher.strip() or None,
            "language": language.strip() or None,
            "published": published.strip() or None,
            "isbn": isbn.strip() or None,
            "description": description.strip() or None,
            "reading_direction": reading_direction,
            "category": category,
            "tags": [t.strip() for t in tags.split(",") if t.strip()],
        },
    )
    if rename_file:
        service.rename_file(session, book)
    return RedirectResponse(f"/library/{book_id}", status_code=303)


@router.post("/{book_id}/delete")
def delete_book(
    book_id: str,
    session: Session = Depends(get_session),
):
    # Deleting means deleting: the confirmation dialog is the consent, so the
    # file leaves the disk with the record (no redundant checkbox).
    book = repository.get_book(session, book_id)
    if book is not None:
        service.delete_books(session, [book], delete_files=True)
    return RedirectResponse("/library", status_code=303)


@router.post("/bulk")
def bulk_action(
    action: str = Form(...),
    book_ids: list[str] = Form(default=[]),
    target_format: str = Form(""),
    device_profile: str = Form(""),
    session: Session = Depends(get_session),
):
    from urllib.parse import quote

    books = [b for b in (repository.get_book(session, bid) for bid in book_ids) if b]
    if not books and action != "delete":
        return RedirectResponse(
            f"/library?err={quote('Selecione ao menos um livro.')}", status_code=303
        )

    if action == "delete":
        # No "keep file" option: deleting implies removing from disk too.
        service.delete_books(session, books, delete_files=True)
        removed = len(books)
        return RedirectResponse(
            f"/library?ok={quote(f'{removed} livro(s) excluído(s).')}", status_code=303
        )

    if action == "refresh":
        for book in books:
            service.refresh_metadata(session, book)
        return RedirectResponse(
            f"/library?ok={quote(f'Metadados atualizados em {len(books)} livro(s).')}",
            status_code=303,
        )

    if action == "rename":
        for book in books:
            service.rename_file(session, book)
        return RedirectResponse(
            f"/library?ok={quote(f'{len(books)} arquivo(s) renomeado(s).')}", status_code=303
        )

    if action == "convert":
        # Mass conversion: one format/profile for every selected book.
        from app.library.conversions import enqueue_conversion

        queued = 0
        for book in books:
            enqueue_conversion(
                session,
                book,
                target_format=target_format or "auto",
                device_profile=device_profile or "eink_generic",
                keep_original=True,
            )
            queued += 1
        label = (target_format or "auto").upper()
        return RedirectResponse(
            f"/library?ok={quote(f'{queued} conversão(ões) para {label} na fila.')}",
            status_code=303,
        )

    return RedirectResponse("/library", status_code=303)


@router.post("/{book_id}/cover")
async def upload_cover(
    book_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    book = repository.get_book(session, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Livro não encontrado")
    data = await file.read()
    if data:
        service.set_cover(session, book, data)
    return RedirectResponse(f"/library/{book_id}", status_code=303)


@router.get("/{book_id}/cover")
def serve_cover(book_id: str, session: Session = Depends(get_session)):
    book = session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Livro não encontrado")
    path = resolve_cover_path(book.cover_path)
    if path is None:
        raise HTTPException(status_code=404, detail="Sem capa")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{book_id}/file")
def serve_file(book_id: str, session: Session = Depends(get_session)):
    book = session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Livro não encontrado")
    path = resolve_library_path(book.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Arquivo ausente")
    return FileResponse(path, filename=f"{book.title}.{book.format}")


def _to_float(value: str) -> float | None:
    try:
        return float(value.replace(",", ".")) if value.strip() else None
    except ValueError:
        return None
