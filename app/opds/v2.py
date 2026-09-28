"""OPDS 2.0 catalog endpoints (JSON based)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.base import get_session
from app.database.models import Book
from app.library.repository import authors, categories
from app.opds import publication, queries, urls
from app.opds.constants import OPDS2_TYPE
from app.security.auth import require_opds_auth

router = APIRouter(
    prefix="/opds/v2",
    tags=["OPDS 2.0"],
    dependencies=[Depends(require_opds_auth)],
)

DEFAULT_PER_PAGE = 30


def _json(document: dict) -> Response:
    return Response(
        content=json.dumps(document, ensure_ascii=False),
        media_type=OPDS2_TYPE,
    )


def _publications_feed(
    *,
    title: str,
    self_href: str,
    builder,
    page,
    session: Session,
) -> Response:
    picks = [publication.publication(book, [book]) for book in page.items]
    document = publication.feed(
        title=title,
        self_href=self_href,
        publications=picks,
        next_href=builder(page.page + 1, page.per_page) if page.has_next else None,
        prev_href=builder(page.page - 1, page.per_page) if page.has_prev else None,
    )
    return _json(document)


@router.get("")
@router.get("/")
def v2_root(session: Session = Depends(get_session)) -> Response:
    """Catalog root: recent publications plus navigation links."""
    page = queries.list_books(session, page=1, per_page=20, sort="added_desc")
    document = publication.feed(
        title=get_settings().app_name,
        self_href=urls.v2_root(),
        publications=[
            publication.publication(book, [book])
            for book in page.items
        ],
        navigation=[
            publication.navigation_item("Novidades", urls.v2_path("new"), description="Adicionados recentemente"),
            publication.navigation_item("Todos os livros", urls.v2_path("all"), description="Catálogo completo"),
            publication.navigation_item("Autores", urls.v2_path("authors")),
            publication.navigation_item("Categorias", urls.v2_path("categories")),
        ],
    )
    return _json(document)


@router.get("/new")
def v2_new(
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=100),
    session: Session = Depends(get_session),
) -> Response:
    result = queries.list_books(session, page=page, per_page=per_page, sort="added_desc")
    return _publications_feed(
        title="Novidades",
        self_href=urls.v2_path(f"new?page={page}&per_page={per_page}"),
        builder=lambda p, pp: urls.v2_path(f"new?page={p}&per_page={pp}"),
        page=result,
        session=session,
    )


@router.get("/all")
def v2_all(
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=100),
    session: Session = Depends(get_session),
) -> Response:
    result = queries.list_books(session, page=page, per_page=per_page, sort="title_asc")
    return _publications_feed(
        title="Todos os livros",
        self_href=urls.v2_path(f"all?page={page}&per_page={per_page}"),
        builder=lambda p, pp: urls.v2_path(f"all?page={p}&per_page={pp}"),
        page=result,
        session=session,
    )


@router.get("/search")
def v2_search(
    q: str = Query("", alias="q"),
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=100),
    session: Session = Depends(get_session),
) -> Response:
    result = queries.list_books(session, page=page, per_page=per_page, query=q)
    return _publications_feed(
        title=f"Busca: {q}" if q else "Busca",
        self_href=urls.v2_path(f"search?q={q}"),
        builder=lambda p, pp: urls.v2_path(f"search?q={q}&page={p}&per_page={pp}"),
        page=result,
        session=session,
    )


@router.get("/authors")
def v2_authors(session: Session = Depends(get_session)) -> Response:
    document = publication.feed(
        title="Autores",
        self_href=urls.v2_path("authors"),
        navigation=[
            publication.navigation_item(f"{name} ({count})", urls.v2_path(f"authors/{name}"))
            for name, count in authors(session)
        ],
    )
    return _json(document)


@router.get("/authors/{name}")
def v2_author(
    name: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=100),
    session: Session = Depends(get_session),
) -> Response:
    result = queries.list_books(session, page=page, per_page=per_page, author=name)
    return _publications_feed(
        title=f"Autor: {name}",
        self_href=urls.v2_path(f"authors/{name}"),
        builder=lambda p, pp: urls.v2_path(f"authors/{name}?page={p}&per_page={pp}"),
        page=result,
        session=session,
    )


@router.get("/categories")
def v2_categories(session: Session = Depends(get_session)) -> Response:
    document = publication.feed(
        title="Categorias",
        self_href=urls.v2_path("categories"),
        navigation=[
            publication.navigation_item(
                f"{row['category'].name} ({row['count']})",
                urls.v2_path(f"categories/{row['category'].slug}"),
            )
            for row in categories(session)
        ],
    )
    return _json(document)


@router.get("/categories/{slug}")
def v2_category(
    slug: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=100),
    session: Session = Depends(get_session),
) -> Response:
    category = queries.get_category(session, slug)
    if category is None:
        raise HTTPException(status_code=404, detail="Categoria não encontrada")
    result = queries.list_books(session, page=page, per_page=per_page, category_slug=slug)
    return _publications_feed(
        title=f"Categoria: {category.name}",
        self_href=urls.v2_path(f"categories/{slug}"),
        builder=lambda p, pp: urls.v2_path(f"categories/{slug}?page={p}&per_page={pp}"),
        page=result,
        session=session,
    )


@router.get("/books/{book_id}")
def v2_book(book_id: str, session: Session = Depends(get_session)) -> Response:
    book = session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Livro não encontrado")
    return _json(publication.publication(book, [book]))


__all__ = ["router"]
