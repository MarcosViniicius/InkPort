"""REST API for library books."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.api import serializers
from app.api.deps import Pagination, pagination, require_api
from app.api.schemas import BookUpdate, BulkDelete
from app.database.base import get_session
from app.library import repository, service
from app.opds import queries as opds_queries

router = APIRouter(prefix="/api/books", tags=["API: livros"], dependencies=[Depends(require_api)])


def _get_or_404(session: Session, book_id: str):
    book = repository.get_book(session, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Livro não encontrado")
    return book


@router.get("")
def list_books(
    q: str | None = None,
    category_id: int | None = None,
    author: str | None = None,
    series: str | None = None,
    tag: str | None = None,
    content_type: str | None = None,
    fmt: str | None = None,
    source: str | None = None,
    sort: str = "added_desc",
    page: Pagination = Depends(pagination),
    session: Session = Depends(get_session),
) -> dict:
    result = repository.search(
        session,
        repository.BookQuery(
            q=q,
            category_id=category_id,
            author=author,
            series=series,
            tag=tag,
            content_type=content_type,
            fmt=fmt,
            source=source,
            sort=sort,
            page=page.page,
            per_page=page.per_page,
        ),
    )
    return {
        "items": [serializers.book_summary(book) for book in result.items],
        "total": result.total,
        "page": result.page,
        "pages": result.pages,
        "per_page": result.per_page,
    }


@router.get("/facets")
def facets(session: Session = Depends(get_session)) -> dict:
    return {
        "categories": [
            {"id": row["category"].id, "name": row["category"].name, "slug": row["category"].slug, "count": row["count"]}
            for row in repository.categories(session)
        ],
        "authors": [{"name": name, "count": count} for name, count in repository.authors(session)],
        "series": [{"name": name, "count": count} for name, count in repository.series(session)],
        "tags": [{"name": name, "count": count} for name, count in repository.tags(session)],
        "formats": [{"format": fmt, "count": count} for fmt, count in repository.formats(session)],
        "content_types": repository.content_type_counts(session),
        "total_size": repository.total_size(session),
    }


@router.get("/{book_id}")
def get_book(book_id: str, session: Session = Depends(get_session)) -> dict:
    book = _get_or_404(session, book_id)
    data = serializers.book_detail(book)
    data["variants"] = [
        serializers.book_summary(variant)
        for variant in opds_queries.variants(session, book)
    ]
    return data


@router.patch("/{book_id}")
def update_book(
    book_id: str, payload: BookUpdate, session: Session = Depends(get_session)
) -> dict:
    book = _get_or_404(session, book_id)
    values = payload.model_dump(exclude_unset=True, exclude={"rename_file"})
    service.update_metadata(session, book, values)
    if payload.rename_file:
        service.rename_file(session, book)
    return serializers.book_detail(book)


@router.delete("/{book_id}")
def delete_book(
    book_id: str,
    delete_files: bool = Query(True),
    session: Session = Depends(get_session),
) -> dict:
    book = _get_or_404(session, book_id)
    service.delete_books(session, [book], delete_files=delete_files)
    return {"deleted": 1}


@router.post("/bulk-delete")
def bulk_delete(payload: BulkDelete, session: Session = Depends(get_session)) -> dict:
    books = []
    for book_id in payload.book_ids:
        book = repository.get_book(session, book_id)
        if book is not None:
            books.append(book)
    count = service.delete_books(session, books, delete_files=payload.delete_files)
    return {"deleted": count}


@router.post("/{book_id}/rename")
def rename_book(book_id: str, session: Session = Depends(get_session)) -> dict:
    book = _get_or_404(session, book_id)
    service.rename_file(session, book)
    return serializers.book_detail(book)


@router.post("/{book_id}/move")
def move_book(
    book_id: str,
    category: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    book = _get_or_404(session, book_id)
    service.move_to_category(session, book, category)
    return serializers.book_detail(book)


@router.post("/{book_id}/refresh")
def refresh_book(book_id: str, session: Session = Depends(get_session)) -> dict:
    book = _get_or_404(session, book_id)
    service.refresh_metadata(session, book)
    return serializers.book_detail(book)


@router.post("/{book_id}/cover")
async def upload_cover(
    book_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> dict:
    book = _get_or_404(session, book_id)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Arquivo vazio")
    service.set_cover(session, book, data)
    return {"ok": True, "has_cover": True}
