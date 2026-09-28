"""OPDS 1.2 acquisition feeds: recent, all, search and per-facet listings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.base import get_session
from app.devices.registry import get_profile
from app.opds import queries, urls
from app.opds.constants import ACQUISITION_TYPE, OPENSEARCH_TYPE
from app.opds.v1._common import DEFAULT_PER_PAGE, paged_feed

router = APIRouter()


@router.get("/new")
def opds_new(
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=100),
    session: Session = Depends(get_session),
) -> Response:
    result = queries.list_books(session, page=page, per_page=per_page, sort="added_desc")
    return paged_feed(
        title="Novidades",
        feed_id=urls.acquisition("/opds/new"),
        self_href=urls.acquisition("/opds/new", {"page": page, "per_page": per_page}),
        page=result,
        builder=lambda p, pp: urls.acquisition("/opds/new", {"page": p, "per_page": pp}),
        session=session,
    )


@router.get("/all")
def opds_all(
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=100),
    sort: str = Query("title_asc"),
    session: Session = Depends(get_session),
) -> Response:
    result = queries.list_books(session, page=page, per_page=per_page, sort=sort)
    query = {"page": page, "per_page": per_page, "sort": sort}
    return paged_feed(
        title="Todos os livros",
        feed_id=urls.acquisition("/opds/all"),
        self_href=urls.acquisition("/opds/all", query),
        page=result,
        builder=lambda p, pp: urls.acquisition("/opds/all", {"page": p, "per_page": pp, "sort": sort}),
        session=session,
    )


@router.get("/authors/{name}")
def opds_author(
    name: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=100),
    session: Session = Depends(get_session),
) -> Response:
    result = queries.list_books(session, page=page, per_page=per_page, author=name)
    return paged_feed(
        title=f"Autor: {name}",
        feed_id=urls.author_feed(name),
        self_href=urls.author_feed(name, page, per_page),
        page=result,
        builder=lambda p, pp: urls.author_feed(name, p, pp),
        session=session,
    )


@router.get("/categories/{slug}")
def opds_category(
    slug: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=100),
    session: Session = Depends(get_session),
) -> Response:
    category = queries.get_category(session, slug)
    if category is None:
        raise HTTPException(status_code=404, detail="Categoria não encontrada")
    result = queries.list_books(session, page=page, per_page=per_page, category_slug=slug)
    return paged_feed(
        title=f"Categoria: {category.name}",
        feed_id=urls.category_feed(slug),
        self_href=urls.category_feed(slug, page, per_page),
        page=result,
        builder=lambda p, pp: urls.category_feed(slug, p, pp),
        session=session,
    )


@router.get("/series/{name}")
def opds_series_books(
    name: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=100),
    session: Session = Depends(get_session),
) -> Response:
    result = queries.list_books(session, page=page, per_page=per_page, series=name, sort="title_asc")
    return paged_feed(
        title=f"Série: {name}",
        feed_id=urls.series_feed(name),
        self_href=urls.series_feed(name, page, per_page),
        page=result,
        builder=lambda p, pp: urls.series_feed(name, p, pp),
        session=session,
    )


@router.get("/tags/{name}")
def opds_tag_books(
    name: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=100),
    session: Session = Depends(get_session),
) -> Response:
    result = queries.list_books(session, page=page, per_page=per_page, tag=name)
    return paged_feed(
        title=f"Tag: {name}",
        feed_id=urls.tag_feed(name),
        self_href=urls.tag_feed(name, page, per_page),
        page=result,
        builder=lambda p, pp: urls.tag_feed(name, p, pp),
        session=session,
    )


@router.get("/search.xml")
def opds_search_description() -> Response:
    base = get_settings().base_url_clean
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">\n'
        "  <ShortName>Biblioteca</ShortName>\n"
        "  <Description>Buscar na biblioteca</Description>\n"
        "  <InputEncoding>UTF-8</InputEncoding>\n"
        f'  <Url type="{ACQUISITION_TYPE}" template="{base}/opds/search?q={{searchTerms}}"/>\n'
        "</OpenSearchDescription>\n"
    )
    return Response(content=xml.encode("utf-8"), media_type=OPENSEARCH_TYPE)


@router.get("/search")
def opds_search(
    q: str = Query("", alias="q"),
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=100),
    session: Session = Depends(get_session),
) -> Response:
    result = queries.list_books(session, page=page, per_page=per_page, query=q)
    return paged_feed(
        title=f"Busca: {q}" if q else "Busca",
        feed_id=urls.search_opds(q),
        self_href=urls.search_opds(q, page, per_page),
        page=result,
        builder=lambda p, pp: urls.search_opds(q, p, pp),
        session=session,
    )


@router.get("/device/{slug}")
def opds_device(
    slug: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=100),
    session: Session = Depends(get_session),
) -> Response:
    """Catalog tuned for one device profile.

    Each entry offers that device's own file first, falling back to the
    universal (adaptive) EPUB. Point the e-reader at this URL to get exactly
    the file built for it.
    """
    profile = get_profile(slug, session)
    result = queries.list_books(
        session, page=page, per_page=per_page, sort="title_asc", collapse=True
    )
    base = f"/opds/device/{slug}"
    return paged_feed(
        title=f"Catálogo para {profile.name}",
        feed_id=urls.acquisition(base),
        self_href=urls.acquisition(base, {"page": page, "per_page": per_page}),
        page=result,
        builder=lambda p, pp: urls.acquisition(base, {"page": p, "per_page": pp}),
        session=session,
        variant_resolver=lambda book: queries.variants_for_device(session, book, slug),
    )
