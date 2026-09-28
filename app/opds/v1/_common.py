"""Shared helpers for the OPDS 1.2 endpoint modules."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from fastapi import Response
from sqlalchemy.orm import Session

from app.opds import entries, feeds, urls
from app.opds.atom import to_bytes
from app.opds.constants import ACQUISITION_TYPE

DEFAULT_PER_PAGE = 30


def xml_response(root: ET.Element, kind: str) -> Response:
    return Response(content=to_bytes(root), media_type=kind)


def paged_feed(
    *,
    title: str,
    feed_id: str,
    self_href: str,
    page,
    builder,
    session: Session,
    subsections: list[tuple[str, str, str]] | None = None,
    navigation_entries: list[tuple[str, str]] | None = None,
    variant_resolver=None,
) -> Response:
    """Build an acquisition feed with variants, pagination and search links.

    ``subsections`` adds feed-level navigation links (standard, but ignored by
    very simple clients). ``navigation_entries`` adds real ``<entry>`` items so
    clients such as CrossPoint can actually browse into the catalog.
    ``variant_resolver(book)`` picks which files to offer for each book.
    """
    root = feeds.build_feed(title=title, feed_id=feed_id, self_href=self_href)
    feeds.add_start_link(root, urls.root())
    feeds.add_search_links(
        root,
        description_href=urls.search_description(),
        search_href=urls.search_opds(""),
    )
    for sub_title, sub_href, sub_kind in subsections or []:
        feeds.add_subsection_link(root, sub_title, sub_href, kind=sub_kind)

    resolve = variant_resolver or (lambda book: [book])
    for book in page.items:
        entries.book_entry(root, book, resolve(book))

    for nav_title, nav_href in navigation_entries or []:
        entries.navigation_entry(root, nav_title, nav_href, None)

    next_href = builder(page.page + 1, page.per_page) if page.has_next else None
    prev_href = builder(page.page - 1, page.per_page) if page.has_prev else None
    feeds.add_pagination(
        root,
        kind=ACQUISITION_TYPE,
        start_index=(page.page - 1) * page.per_page,
        per_page=page.per_page,
        total=page.total,
        next_href=next_href,
        prev_href=prev_href,
    )
    return xml_response(root, ACQUISITION_TYPE)
