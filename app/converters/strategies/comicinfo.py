"""Build ComicInfo.xml so CBZ output keeps its metadata."""

from __future__ import annotations

from xml.sax.saxutils import escape

from app.converters.base import ConversionRequest


def build_comicinfo(request: ConversionRequest) -> str:
    meta = request.metadata
    direction = (
        "YesAndRightToLeft"
        if (request.options.get("reading_direction") or meta.reading_direction) == "rtl"
        else "No"
    )
    fields = {
        "Title": meta.title or request.source.stem,
        "Series": meta.series or "",
        "Number": int(meta.series_index) if meta.series_index else "",
        "Writer": meta.author or "",
        "Publisher": meta.publisher or "",
        "Summary": meta.description or "",
        "LanguageISO": meta.language or "",
        "PageCount": meta.page_count or "",
        "Manga": direction,
        "AgeRating": meta.age_rating or "",
        "Genre": ", ".join(meta.tags or []),
    }
    body = "".join(
        f"  <{key}>{escape(str(value))}</{key}>\n"
        for key, value in fields.items()
        if value not in ("", None)
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema">\n'
        f"{body}</ComicInfo>\n"
    )


def reading_direction(request: ConversionRequest) -> str:
    return (
        request.options.get("reading_direction")
        or request.metadata.reading_direction
        or "ltr"
    )
