"""The EPUB package document (``content.opf``)."""

from __future__ import annotations

import html
from datetime import UTC, datetime

from app.converters.epub.model import EpubMeta, mime_for


def build_opf(
    meta: EpubMeta,
    images: list[str],
    pages: list[str],
    cover_href: str | None,
    include_title_page: bool,
) -> str:
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="styles.css" media-type="text/css"/>',
    ]
    if cover_href:
        manifest.append(
            '<item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>'
        )
        manifest.append(
            f'<item id="cover-image" href="{cover_href}" media-type="image/jpeg" '
            'properties="cover-image"/>'
        )
    if include_title_page:
        manifest.append(
            '<item id="title-page" href="title.xhtml" media-type="application/xhtml+xml"/>'
        )
    manifest.extend(
        f'<item id="page{i}" href="{href}" media-type="application/xhtml+xml"/>'
        for i, href in enumerate(pages)
    )
    manifest.extend(
        f'<item id="img{i}" href="{href}" media-type="{mime_for(href)}"/>'
        for i, href in enumerate(images)
    )

    spine_items: list[str] = []
    if cover_href:
        spine_items.append('<itemref idref="cover-page"/>')
    if include_title_page:
        spine_items.append('<itemref idref="title-page"/>')
    spine_items.extend(f'<itemref idref="page{i}"/>' for i in range(len(pages)))
    spine = "\n".join("    " + item for item in spine_items)

    direction = (
        ' page-progression-direction="rtl"' if meta.reading_direction == "rtl" else ""
    )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid" xml:lang="' + html.escape(meta.language) + '">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:opf="http://www.idpf.org/2007/opf">\n'
        f'    <dc:identifier id="bookid">{html.escape(meta.identifier)}</dc:identifier>\n'
        f"    <dc:title>{html.escape(meta.title)}</dc:title>\n"
        f"    <dc:language>{html.escape(meta.language)}</dc:language>\n"
        f"{_creator(meta)}"
        f"{_publisher(meta)}"
        f"{_description(meta)}"
        f"{_subjects(meta)}"
        f'    <meta property="dcterms:modified">{_now()}</meta>\n'
        f'    <meta name="generator" content="{html.escape(meta.generator)}"/>\n'
        f"{_series(meta)}"
        f"{_rendition(meta)}"
        f"{_cover_meta(cover_href)}"
        "  </metadata>\n"
        "  <manifest>\n    " + "\n    ".join(manifest) + "\n  </manifest>\n"
        f'  <spine toc="ncx"{direction}>\n{spine}\n  </spine>\n'
        "</package>\n"
    )


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _creator(meta: EpubMeta) -> str:
    if not meta.author:
        return ""
    return (
        f'    <dc:creator id="author">{html.escape(meta.author)}</dc:creator>\n'
        '    <meta refines="#author" property="role" scheme="marc:relators">aut</meta>\n'
    )


def _publisher(meta: EpubMeta) -> str:
    if not meta.publisher:
        return ""
    return f"    <dc:publisher>{html.escape(meta.publisher)}</dc:publisher>\n"


def _description(meta: EpubMeta) -> str:
    if not meta.description:
        return ""
    return f"    <dc:description>{html.escape(meta.description)}</dc:description>\n"


def _subjects(meta: EpubMeta) -> str:
    if not meta.subjects:
        return ""
    return "".join(
        f"    <dc:subject>{html.escape(subject)}</dc:subject>\n"
        for subject in meta.subjects
    )


def _series(meta: EpubMeta) -> str:
    if not meta.series:
        return ""
    out = f'    <meta name="calibre:series" content="{html.escape(meta.series)}"/>\n'
    if meta.series_index is not None:
        out += f'    <meta name="calibre:series_index" content="{meta.series_index}"/>\n'
    out += (
        '    <meta property="belongs-to-collection" id="series">'
        f"{html.escape(meta.series)}</meta>\n"
    )
    if meta.series_index is not None:
        out += f'    <meta refines="#series" property="group-position">{meta.series_index}</meta>\n'
    return out


def _rendition(meta: EpubMeta) -> str:
    if not meta.fixed_layout:
        return ""
    return (
        '    <meta property="rendition:layout">pre-paginated</meta>\n'
        '    <meta property="rendition:spread">none</meta>\n'
    )


def _cover_meta(cover_href: str | None) -> str:
    if not cover_href:
        return ""
    return '    <meta name="cover" content="cover-image"/>\n'
