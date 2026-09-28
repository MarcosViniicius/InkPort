"""OPDS 2.0 JSON document builders (publications and feeds)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.database.models import Book
from app.library.formats import media_type_for
from app.opds import urls
from app.opds.constants import OPDS2_TYPE
from app.opds.queries import rank_variants


def publication(book: Book, variants: list[Book] | None = None) -> dict:
    variants = variants or [book]
    cover_book = next((v for v in variants if v.cover_path), book)

    metadata = {
        "identifier": f"urn:opds:book:{book.id}",
        "title": book.title,
        "modified": _iso(book.updated_at or book.added_at),
    }
    if book.author:
        metadata["author"] = {"name": book.author}
        metadata["sortAs"] = book.author.lower()
    if book.language:
        metadata["language"] = book.language
    if book.publisher:
        metadata["publisher"] = book.publisher
    if book.description:
        metadata["description"] = book.description
    if book.published:
        metadata["published"] = f"{book.published}-01-01T00:00:00Z"
    if book.series:
        metadata["belongsTo"] = {
            "series": {"name": book.series},
            "position": book.series_index,
        }
    if book.page_count:
        metadata["numberOfPages"] = book.page_count
    if book.tags:
        metadata["subject"] = [{"name": tag.name} for tag in book.tags]

    links: list[dict] = []
    for variant in rank_variants(variants):
        links.append(
            {
                "rel": "http://opds-spec.org/acquisition",
                "href": urls.download(variant.id),
                "type": media_type_for(variant.format, variant.media_type),
                "properties": {
                    "availability": {"state": "available"},
                    "format": variant.format,
                },
            }
        )

    images = []
    if cover_book.cover_path:
        images.append({"href": urls.cover(cover_book.id), "type": "image/jpeg"})
        images.append({"href": urls.thumbnail(cover_book.id), "type": "image/jpeg"})

    return {
        "metadata": metadata,
        "links": links,
        "images": images,
    }


def feed(
    *,
    title: str,
    self_href: str,
    modified: datetime | None = None,
    publications: list[dict] | None = None,
    navigation: list[dict] | None = None,
    next_href: str | None = None,
    prev_href: str | None = None,
) -> dict:
    links = [{"rel": "self", "href": self_href, "type": OPDS2_TYPE}]
    links.append({"rel": "start", "href": urls.v2_root(), "type": OPDS2_TYPE})
    if next_href:
        links.append({"rel": "next", "href": next_href, "type": OPDS2_TYPE})
    if prev_href:
        links.append({"rel": "previous", "href": prev_href, "type": OPDS2_TYPE})

    document: dict = {
        "metadata": {"title": title, "modified": _iso(modified)},
        "links": links,
    }
    if publications is not None:
        document["publications"] = publications
    if navigation is not None:
        document["navigation"] = navigation
    return document


def navigation_item(title: str, href: str, *, description: str | None = None) -> dict:
    item = {"title": title, "href": href, "type": OPDS2_TYPE}
    if description:
        item["description"] = description
    return item


def _iso(value: datetime | None) -> str:
    value = value or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
