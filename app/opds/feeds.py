"""Composing OPDS feed documents (navigation and acquisition)."""

from __future__ import annotations

from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from app.opds.atom import atom, link, make, opds, sub
from app.opds.constants import (
    ACQUISITION_TYPE,
    NAVIGATION_TYPE,
    OPENSEARCH_NS,
    OPENSEARCH_TYPE,
    REL_NEXT,
    REL_PREV,
    REL_SEARCH,
    REL_SELF,
    REL_START,
    REL_SUBSECTION,
)


def opensearch(tag: str) -> str:
    return f"{{{OPENSEARCH_NS}}}{tag}"


def build_feed(
    *,
    title: str,
    feed_id: str,
    self_href: str,
    kind: str = ACQUISITION_TYPE,
    updated: datetime | None = None,
) -> ET.Element:
    feed = make(atom("feed"))
    sub(feed, atom("id"), feed_id)
    sub(feed, atom("title"), title)
    sub(feed, atom("updated"), _iso(updated))
    link(feed, REL_SELF, self_href, type=kind)
    # Feed-level author: RFC 4287 requires every entry to have an author *unless*
    # the feed has one. Books without a known author therefore carry no author at
    # all -- clients fall back to the title instead of saving files named
    # "Autores desconhecidos".
    author = sub(feed, atom("author"), None)
    sub(author, atom("name"), _feed_author(title))
    return feed


def _feed_author(fallback: str) -> str:
    from app.config import get_settings

    return get_settings().app_name or fallback


def add_start_link(feed: ET.Element, href: str) -> None:
    link(feed, REL_START, href, type=NAVIGATION_TYPE, title="Catálogo")


def add_search_links(feed: ET.Element, *, description_href: str, search_href: str) -> None:
    link(feed, REL_SEARCH, description_href, type=OPENSEARCH_TYPE, title="Buscar")
    link(
        feed,
        REL_SEARCH,
        search_href,
        type=ACQUISITION_TYPE,
        title="Resultados da busca",
    )


def add_pagination(
    feed: ET.Element,
    *,
    kind: str,
    start_index: int,
    per_page: int,
    total: int,
    next_href: str | None = None,
    prev_href: str | None = None,
) -> None:
    sub(feed, opensearch("totalResults"), str(total))
    sub(feed, opensearch("startIndex"), str(start_index))
    sub(feed, opensearch("itemsPerPage"), str(per_page))
    if next_href:
        link(feed, REL_NEXT, next_href, type=kind)
    if prev_href:
        link(feed, REL_PREV, prev_href, type=kind)


def add_facet(
    feed: ET.Element,
    *,
    title: str,
    href: str,
    active: bool = False,
    facet_group: str | None = None,
) -> None:
    link(
        feed,
        "http://opds-spec.org/facet",
        href,
        type=NAVIGATION_TYPE,
        title=title,
    )
    feed[-1].set(f"{opds('facetGroup')}", facet_group or "Filtros")
    if active:
        feed[-1].set(f"{opds('activeFacet')}", "true")


def add_sort_link(feed: ET.Element, rel: str, href: str, title: str) -> None:
    link(feed, rel, href, type=NAVIGATION_TYPE, title=title)


def add_subsection_link(
    feed: ET.Element, title: str, href: str, *, kind: str = NAVIGATION_TYPE
) -> None:
    """Feed-level navigation link (the client lists it, it is not a book)."""
    link(feed, REL_SUBSECTION, href, type=kind, title=title)


def pagination_hrefs(
    builder,
    *,
    page: int,
    pages: int,
    per_page: int,
) -> tuple[str | None, str | None]:
    """Return (next_href, prev_href) using a builder(page) callable."""
    next_href = builder(page + 1) if page < pages else None
    prev_href = builder(page - 1) if page > 1 else None
    return next_href, prev_href


def _iso(value: datetime | None) -> str:
    value = value or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
