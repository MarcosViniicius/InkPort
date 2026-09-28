"""XHTML / NCX templates for generated EPUBs."""

from __future__ import annotations

import html

from app.converters.epub.model import EpubMeta

_HEAD = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<!DOCTYPE html>\n'
    '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{lang}">\n'
    "<head><title>{title}</title>\n"
    '<meta charset="utf-8"/>\n'
    "{viewport}"
    '<link rel="stylesheet" type="text/css" href="{css}"/>\n'
    "</head>\n"
)

# Files under "text/" need one level up; files at the OEBPS root do not.
_CSS_FROM_TEXT = "../styles.css"
_CSS_FROM_ROOT = "styles.css"

_VIEWPORT_FIXED = (
    '<meta name="viewport" content="width=device-width, height=device-height, '
    'initial-scale=1.0, maximum-scale=1.0, user-scalable=no"/>\n'
)


def page_xhtml(image_href: str, *, index: int, total: int, lang: str = "pt") -> str:
    head = _HEAD.format(
        title="Página", lang=lang, viewport=_VIEWPORT_FIXED, css=_CSS_FROM_TEXT
    )
    return (
        f"{head}"
        '<body class="page"><div class="page">'
        f'<img src="{html.escape(image_href)}" alt="Página {index} de {total}"/>'
        "</div></body></html>\n"
    )


def title_xhtml(meta: EpubMeta) -> str:
    head = _HEAD.format(
        title=html.escape(meta.title),
        lang=html.escape(meta.language),
        viewport="",
        css=_CSS_FROM_ROOT,
    )
    author = (
        f'<p class="book-author">{html.escape(meta.author)}</p>' if meta.author else ""
    )
    return (
        f'{head}<body><section class="titlepage">'
        f'<h1 class="book-title">{html.escape(meta.title)}</h1>{author}'
        "</section></body></html>\n"
    )


def cover_xhtml(cover_href: str, meta: EpubMeta) -> str:
    # This page lives at the OEBPS root, so its links must not climb a level.
    head = _HEAD.format(
        title="Capa", lang=html.escape(meta.language), viewport="", css=_CSS_FROM_ROOT
    )
    return (
        f'{head}<body class="cover"><img src="{html.escape(cover_href)}" '
        f'alt="{html.escape(meta.title)}"/></body></html>\n'
    )


def nav_xhtml(entries: list[tuple[str, str]]) -> str:
    items = "\n".join(
        f'      <li><a href="{html.escape(href)}">{html.escape(title)}</a></li>'
        for title, href in entries
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="pt">\n'
        '<head><title>Sumário</title><meta charset="utf-8"/></head>\n'
        "<body>\n"
        '  <nav epub:type="toc" id="toc"><h1>Sumário</h1>\n    <ol>\n'
        f"{items}\n    </ol>\n  </nav>\n</body></html>\n"
    )


def ncx_xml(meta: EpubMeta, entries: list[tuple[str, str]]) -> str:
    points = "\n".join(
        f'    <navPoint id="navPoint-{i}" playOrder="{i}">'
        f"<navLabel><text>{html.escape(title)}</text></navLabel>"
        f'<content src="{html.escape(href)}"/></navPoint>'
        for i, (title, href) in enumerate(entries, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        "  <head>\n"
        f'    <meta name="dtb:uid" content="{html.escape(meta.identifier)}"/>\n'
        '    <meta name="dtb:depth" content="1"/>\n'
        '    <meta name="dtb:totalPageCount" content="0"/>\n'
        '    <meta name="dtb:maxPageNumber" content="0"/>\n'
        "  </head>\n"
        f"  <docTitle><text>{html.escape(meta.title)}</text></docTitle>\n"
        "  <navMap>\n"
        f"{points}\n"
        "  </navMap>\n</ncx>\n"
    )
