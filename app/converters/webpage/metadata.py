"""Metadata extraction (port of ``get_metadata.ts``)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from lxml import etree

from app.converters.webpage.tags import (
    DATE_METATAGS,
    DESCRIPTION_METATAGS,
    PUBLISHER_METATAGS,
    TAGS_METATAGS,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PageMetadata:
    title: str = ""
    author: str = ""
    publisher: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    date: str | None = None
    identifier: str = ""
    language: str = "en"


def get_metadata(doc: etree._Element, url: str) -> PageMetadata:
    return PageMetadata(
        title=_title(doc, url),
        date=_date(doc),
        author=_author(doc),
        publisher=_publisher(doc) or url,
        identifier=url,
        description=_metatag(doc, DESCRIPTION_METATAGS) or "",
        tags=_tags(doc),
        language=_language(doc),
    )


def _title(doc: etree._Element, url: str) -> str:
    for node in doc.iter("title"):
        text = (node.text or "").strip()
        if text:
            return text
    from urllib.parse import urlparse

    try:
        return urlparse(url).hostname or url
    except ValueError:
        return url


def _date(doc: etree._Element) -> str | None:
    candidates = [
        _attr(doc, '//meta[@itemprop="datePublished"][@content]', "content"),
        _attr(doc, '//time[@itemprop="startDate"][@datetime]', "datetime"),
        _metatag(doc, DATE_METATAGS),
    ]
    for value in candidates:
        if value and value.strip():
            return value.strip()
    return None


def _author(doc: etree._Element) -> str:
    metatag = _metatag(doc, ["author"])
    if metatag:
        return metatag

    structured = _attr(
        doc, '//*[@itemprop="author"]//meta[@itemprop="name"][@content]', "content"
    )
    if structured:
        return structured.strip()

    author = doc.xpath('//*[@itemprop="author"]')
    if author:
        return (author[0].text_content() or "").strip()

    return ""


def _publisher(doc: etree._Element) -> str:
    structured = _attr(
        doc, '//*[@itemprop="publisher"]//meta[@itemprop="name"][@content]', "content"
    )
    if structured:
        return structured.strip()
    return _metatag(doc, PUBLISHER_METATAGS) or ""


def _tags(doc: etree._Element) -> list[str]:
    content = _metatag(doc, TAGS_METATAGS)
    if content:
        return [part.strip() for part in content.split(",") if part.strip()]
    return []


def _language(doc: etree._Element) -> str:
    for node in doc.iter("html"):
        lang = (node.get("lang") or node.get("{http://www.w3.org/XML/1998/namespace}lang") or "").strip()
        if lang:
            return lang.split("-")[0].lower() or "en"
    return "en"


def _metatag(doc: etree._Element, names: list[str]) -> str | None:
    for name in names:
        for attribute in ("name", "property"):
            values = doc.xpath(f'//meta[@{attribute}="{name}"][@content]/@content')
            for value in values:
                text = (value or "").strip()
                if text:
                    return text
    return None


def _attr(doc: etree._Element, xpath: str, attribute: str) -> str | None:
    try:
        values = doc.xpath(f"{xpath}/@{attribute}")
    except etree.XPathError:  # pragma: no cover - defensive
        return None
    for value in values:
        if value and value.strip():
            return value
    return None
