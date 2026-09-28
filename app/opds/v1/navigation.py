"""OPDS 1.2 navigation feeds: root, authors, categories, series and tags."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.base import get_session
from app.library.repository import authors, categories, series, tags
from app.opds import entries, feeds, queries, urls
from app.opds.constants import ACQUISITION_TYPE, NAVIGATION_TYPE
from app.opds.v1._common import DEFAULT_PER_PAGE, paged_feed, xml_response

router = APIRouter()

#: Feed-level navigation available from the catalog root.
ROOT_SUBSECTIONS = [
    ("Novidades", "/opds/new", ACQUISITION_TYPE),
    ("Todos os livros", "/opds/all", ACQUISITION_TYPE),
    ("Por dispositivo", "/opds/devices", NAVIGATION_TYPE),
    ("Navegar por autor", "/opds/authors", NAVIGATION_TYPE),
    ("Navegar por categoria", "/opds/categories", NAVIGATION_TYPE),
    ("Navegar por série", "/opds/series", NAVIGATION_TYPE),
    ("Navegar por tag", "/opds/tags", NAVIGATION_TYPE),
]


@router.get("/", include_in_schema=False)
@router.get("", include_in_schema=False)
def opds_root(
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=100),
    session: Session = Depends(get_session),
) -> Response:
    """Catalog root.

    ``OPDS_ROOT_MODE`` decides what shows up here:

    * ``mixed`` (default): the recent books as an *acquisition* feed **plus** the
      catalog sections. Simple clients such as the CrossPoint OPDS browser on
      the Xteink only list acquisition entries, so they need books here.
    * ``navigation``: only the sections, so a client that lists entries can
      browse the categories without wading through book pages.
    * ``books``: only the books (the sections stay available at ``/opds/browse``).
    """
    mode = get_settings().opds_root_mode
    subsections = [(title, urls.acquisition(href), kind) for title, href, kind in ROOT_SUBSECTIONS]
    section_entries = [
        (title, urls.navigation(href) if kind == NAVIGATION_TYPE else urls.acquisition(href))
        for title, href, kind in ROOT_SUBSECTIONS
    ]

    if mode == "navigation":
        root = feeds.build_feed(
            title=get_settings().app_name,
            feed_id=urls.root(),
            self_href=urls.acquisition("/opds"),
        )
        feeds.add_search_links(
            root,
            description_href=urls.search_description(),
            search_href=urls.search_opds(""),
        )
        for sub_title, sub_href, sub_kind in subsections:
            feeds.add_subsection_link(root, sub_title, sub_href, kind=sub_kind)
        # Real entries as well: clients that only list <entry> show the sections,
        # and there is no book page to wade through.
        for nav_title, nav_href in section_entries:
            entries.navigation_entry(root, nav_title, nav_href, None)
        feeds.add_pagination(
            root,
            kind=ACQUISITION_TYPE,
            start_index=0,
            per_page=len(section_entries),
            total=len(section_entries),
            next_href=None,
            prev_href=None,
        )
        return xml_response(root, ACQUISITION_TYPE)

    result = queries.list_books(session, page=page, per_page=per_page, sort="added_desc")
    return paged_feed(
        title=get_settings().app_name,
        feed_id=urls.root(),
        self_href=urls.acquisition("/opds", {"page": page, "per_page": per_page}),
        page=result,
        builder=lambda p, pp: urls.acquisition("/opds", {"page": p, "per_page": pp}),
        session=session,
        subsections=subsections,
        navigation_entries=section_entries if mode == "mixed" else [],
    )


@router.get("/browse")
def opds_browse() -> Response:
    """Navigation feed: the catalog sections, for clients that browse by section.

    Carries the sections both as ``rel="subsection"`` links (the OPDS way) and as
    real ``<entry>`` items. Android readers that only render entries would show
    an empty catalog otherwise, which is why this page exists next to ``/opds``.
    """
    root = feeds.build_feed(
        title=get_settings().app_name,
        feed_id=urls.navigation("/opds/browse"),
        self_href=urls.navigation("/opds/browse"),
        kind=NAVIGATION_TYPE,
    )
    feeds.add_search_links(
        root,
        description_href=urls.search_description(),
        search_href=urls.search_opds(""),
    )
    for title, href, kind in ROOT_SUBSECTIONS:
        feeds.add_subsection_link(root, title, urls.acquisition(href), kind=kind)
    for title, href, kind in ROOT_SUBSECTIONS:
        target = urls.navigation(href) if kind == NAVIGATION_TYPE else urls.acquisition(href)
        entries.navigation_entry(root, title, target)
    feeds.add_pagination(
        root,
        kind=NAVIGATION_TYPE,
        start_index=0,
        per_page=len(ROOT_SUBSECTIONS),
        total=len(ROOT_SUBSECTIONS),
    )
    return xml_response(root, NAVIGATION_TYPE)


@router.get("/devices")
def opds_devices(session: Session = Depends(get_session)) -> Response:
    """Navigation feed listing one catalog per device profile."""
    from app.devices.registry import all_profiles

    root = feeds.build_feed(
        title="Por dispositivo",
        feed_id=urls.navigation("/opds/devices"),
        self_href=urls.navigation("/opds/devices"),
        kind=NAVIGATION_TYPE,
    )
    feeds.add_start_link(root, urls.root())
    for slug, profile in all_profiles(session).items():
        entries.navigation_entry(
            root,
            profile.name,
            urls.acquisition(f"/opds/device/{slug}"),
            profile.description or f"Catálogo pronto para {profile.name}",
        )
    return xml_response(root, NAVIGATION_TYPE)


@router.get("/authors")
def opds_authors(session: Session = Depends(get_session)) -> Response:
    root = feeds.build_feed(
        title="Autores",
        feed_id=urls.navigation("/opds/authors"),
        self_href=urls.navigation("/opds/authors"),
        kind=NAVIGATION_TYPE,
    )
    feeds.add_start_link(root, urls.root())
    for name, count in authors(session):
        entries.navigation_entry(root, f"{name} ({count})", urls.author_feed(name), f"{count} livro(s)")
    return xml_response(root, NAVIGATION_TYPE)


@router.get("/categories")
def opds_categories(session: Session = Depends(get_session)) -> Response:
    root = feeds.build_feed(
        title="Categorias",
        feed_id=urls.navigation("/opds/categories"),
        self_href=urls.navigation("/opds/categories"),
        kind=NAVIGATION_TYPE,
    )
    feeds.add_start_link(root, urls.root())
    for row in categories(session):
        category = row["category"]
        entries.navigation_entry(
            root,
            f"{category.name} ({row['count']})",
            urls.category_feed(category.slug),
            f"{row['count']} livro(s)",
        )
    return xml_response(root, NAVIGATION_TYPE)


@router.get("/series")
def opds_series(session: Session = Depends(get_session)) -> Response:
    root = feeds.build_feed(
        title="Séries",
        feed_id=urls.navigation("/opds/series"),
        self_href=urls.navigation("/opds/series"),
        kind=NAVIGATION_TYPE,
    )
    feeds.add_start_link(root, urls.root())
    for name, count in series(session):
        entries.navigation_entry(root, f"{name} ({count})", urls.series_feed(name), f"{count} livro(s)")
    return xml_response(root, NAVIGATION_TYPE)


@router.get("/tags")
def opds_tags(session: Session = Depends(get_session)) -> Response:
    root = feeds.build_feed(
        title="Tags",
        feed_id=urls.navigation("/opds/tags"),
        self_href=urls.navigation("/opds/tags"),
        kind=NAVIGATION_TYPE,
    )
    feeds.add_start_link(root, urls.root())
    for name, count in tags(session):
        entries.navigation_entry(root, f"{name} ({count})", urls.tag_feed(name), f"{count} livro(s)")
    return xml_response(root, NAVIGATION_TYPE)
