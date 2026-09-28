"""Turning library records into OPDS (Atom) entries."""

from __future__ import annotations

from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from app.database.models import Book
from app.library.formats import media_type_for
from app.opds import urls
from app.opds.atom import atom, dc, link, make, sub
from app.opds.constants import (
    REL_ACQUISITION,
    REL_IMAGE,
    REL_SUBSECTION,
    REL_THUMBNAIL,
)
from app.opds.queries import UNIVERSAL_PROFILES


def book_entry(parent: ET.Element, book: Book, variants: list[Book] | None = None) -> ET.Element:
    variants = variants or [book]
    entry = make(atom("entry"))
    parent.append(entry)

    sub(entry, atom("title"), display_title(book, variants))
    sub(entry, atom("id"), f"urn:opds:book:{book.id}")
    sub(entry, atom("updated"), _iso(book.updated_at or book.added_at))
    # Only a real author: emitting a placeholder ("Autores desconhecidos") made
    # clients that build the file name from title + author save every anonymous
    # book under that useless name. The feed carries a feed-level author, which
    # is what Atom requires in this case.
    if book.author:
        author = sub(entry, atom("author"), None)
        sub(author, atom("name"), book.author)

    if book.language:
        sub(entry, dc("language"), book.language)
    if book.publisher:
        sub(entry, dc("publisher"), book.publisher)
    if book.isbn:
        sub(entry, dc("identifier"), book.isbn)
    if book.published:
        sub(entry, dc("issued"), book.published)
    if book.series:
        sub(entry, atom("category"), None, term=book.series, label=book.series)

    content = make(atom("content"), type="text")
    content.text = _summary(book)
    entry.append(content)

    _cover_links(entry, variants)
    _acquisition_links(entry, variants)
    link(entry, "alternate", urls.book_entry(book.id), type="text/html", title="Detalhes")
    return entry


def navigation_entry(
    parent: ET.Element,
    title: str,
    href: str,
    content: str | None = None,
    *,
    thumbnail: str | None = None,
) -> ET.Element:
    entry = make(atom("entry"))
    parent.append(entry)
    sub(entry, atom("title"), title)
    sub(entry, atom("id"), href)
    sub(entry, atom("updated"), _now())
    sub(entry, atom("content"), content, type="text")
    link(entry, REL_SUBSECTION, href, type="application/atom+xml;profile=opds-catalog;kind=acquisition")
    if thumbnail:
        link(entry, REL_THUMBNAIL, thumbnail, type="image/jpeg")
    return entry


# --- internals ------------------------------------------------------------
def display_title(book: Book, variants: list[Book] | None = None) -> str:
    """Entry title, tagged with the target device when the work has several files.

    The tag exists so two files of the same work do not land on the device under
    the same name (CrossPoint builds the file name from the entry). When there is
    only one file there is nothing to disambiguate -- and a plain title is what
    the reader shows and saves.
    """
    from app.devices.registry import all_profiles

    slug = book.device_profile or ""
    if slug in UNIVERSAL_PROFILES:
        return book.title
    profile = all_profiles().get(slug)
    if profile is None:
        return book.title
    if variants is not None and len(variants) <= 1:
        return book.title
    return f"{book.title} — {profile.name}"


def _cover_links(entry: ET.Element, variants: list[Book]) -> None:
    book = next((v for v in variants if v.cover_path), variants[0])
    if book.cover_path:
        link(entry, REL_IMAGE, urls.cover(book.id), type="image/jpeg")
        link(entry, REL_THUMBNAIL, urls.thumbnail(book.id), type="image/jpeg")


def _acquisition_links(entry: ET.Element, variants: list[Book]) -> None:
    ordered = list(variants)
    seen: set[tuple[str, str]] = set()
    for book in ordered:
        key = (book.format, book.device_profile or "")
        if key in seen:
            continue
        seen.add(key)
        mime = media_type_for(book.format, book.media_type)
        link(
            entry,
            REL_ACQUISITION,
            urls.download(book.id),
            type=mime,
            title=_variant_title(book),
            length=book.file_size or None,
        )


def _variant_title(book: Book) -> str:
    from app.devices.registry import all_profiles

    profile = all_profiles().get(book.device_profile or "")
    if profile and (book.device_profile or "") not in UNIVERSAL_PROFILES:
        return f"Baixar {book.format.upper()} — {profile.name}"
    return f"Baixar {book.format.upper()}"


def _summary(book: Book) -> str:
    if book.description:
        return book.description
    parts = []
    if book.series:
        index = f" #{int(book.series_index)}" if book.series_index else ""
        parts.append(f"{book.series}{index}")
    if book.page_count:
        parts.append(f"{book.page_count} páginas")
    parts.append(book.format.upper())
    return " — ".join(parts)


def _iso(value: datetime | None) -> str:
    if value is None:
        return _now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
