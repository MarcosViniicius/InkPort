"""Sanitisation of untrusted book content (HTML/CSS from inside the files).

Books are user-supplied and therefore hostile input: an EPUB can carry
``<script>``, ``onerror=`` handlers, remote trackers, ``javascript:`` links and
``<iframe>``s. This module strips all of that while keeping layout intact, and
rewrites every *internal* URL to the reader's own ``/reader/<id>/asset/...``
route so relative images, fonts and stylesheets keep working.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable
from urllib.parse import quote

from lxml import html as lxml_html

#: Tags that can execute code, fetch or navigate: always removed.
BLOCKED_TAGS = frozenset(
    {
        "script",
        "iframe",
        "frame",
        "frameset",
        "object",
        "embed",
        "applet",
        "form",
        "input",
        "textarea",
        "select",
        "option",
        "button",
        "base",
        "meta",
        "noscript",
        "template",
        "portal",
    }
)

#: Attributes carrying a URL that must be rewritten (or dropped).
URL_ATTRS = frozenset({"src", "href", "poster", "background", "data", "srcset", "xlink:href"})

_SAFE_SCHEMES = ("data:", "mailto:", "tel:", "about:blank")
_REMOTE_SCHEME = re.compile(r"^(https?:)?//", re.I)
_ANY_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*:", re.I)
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
_CSS_DANGER = re.compile(r"(expression\s*\(|javascript\s*:|vbscript\s*:|behaviou?r\s*:|-moz-binding)", re.I)
_CSS_IMPORT = re.compile(r"@import[^;]*;", re.I)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)

#: Minimal reset injected into every chapter so the iframe never scrolls
#: sideways and text can break anywhere in a narrow device column.
BASE_STYLE = (
    "html{-webkit-text-size-adjust:100%;}"
    "body{overflow-wrap:break-word;word-wrap:break-word;}"
    "img,svg,video{max-width:100%;height:auto;}"
)


def asset_url_factory(book_id: str) -> Callable[[str], str]:
    return lambda path: f"/reader/{book_id}/asset/{quote(path)}"


def chapter_url_factory(book_id: str) -> Callable[[str], str]:
    return lambda path: f"/reader/{book_id}/chapter/{quote(path)}"


def _split(url: str) -> tuple[str, str]:
    fragment = ""
    if "#" in url:
        url, fragment = url.split("#", 1)
        fragment = "#" + fragment
    if "?" in url:
        url = url.split("?", 1)[0]
    return url, fragment


def resolve_reference(raw: str, base: str, *, allow_remote: bool = False):
    """Classify and normalise a URL found inside a book.

    Returns ``("keep", url)`` for fragments/data URIs, ``("remote", url)`` when
    remote access is allowed, ``("internal", path)`` for same-book resources and
    ``("block", "")`` for anything dangerous or escaping the book.
    """
    value = (raw or "").strip()
    if not value:
        return "block", ""
    low = value.lower()
    if low.startswith("#") or low.startswith("data:") or low.startswith("about:blank"):
        return "keep", value
    if low.startswith(("mailto:", "tel:")):
        return "keep", value
    if _REMOTE_SCHEME.match(value):
        return ("remote", value) if allow_remote else ("block", "")
    if _ANY_SCHEME.match(value) and not low.startswith("data:"):
        return ("remote", value) if allow_remote else ("block", "")
    path, fragment = _split(value)
    joined = posixpath.normpath(posixpath.join(base, path))
    if joined in {"", ".", ".."} or joined.startswith("../") or joined.startswith("/"):
        return "block", ""
    return "internal", joined + fragment


def build_document(body_html: str, *, title: str = "", extra_head: str = "") -> str:
    """Wrap trusted, reader-generated HTML in a minimal full document."""
    from html import escape

    return (
        "<!DOCTYPE html>\n<html lang=\"pt-BR\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{escape(title)}</title>\n"
        f"<style id=\"reader-base\">{BASE_STYLE}</style>\n"
        f"{extra_head}\n</head>\n<body>\n{body_html}\n</body>\n</html>"
    )


def _rewrite_css_urls(
    css: str, base: str, *, allow_remote: bool, asset_url: Callable[[str], str]
) -> str:
    def replace(match: re.Match) -> str:
        raw = match.group(2).strip()
        kind, value = resolve_reference(raw, base, allow_remote=allow_remote)
        if kind == "keep":
            return f'url("{value}")'
        if kind == "remote":
            return f'url("{value}")'
        if kind == "internal":
            return f'url("{asset_url(value)}")'
        return 'url("data:,")'

    return _CSS_URL.sub(replace, css)


def sanitize_css(
    css: str,
    *,
    base: str,
    asset_url: Callable[[str], str],
    allow_remote: bool = False,
) -> str:
    """Clean a stylesheet loaded from inside a book (or an inline ``<style>``)."""
    text = _CSS_COMMENT.sub("", css or "")
    text = _CSS_IMPORT.sub("", text)
    text = _CSS_DANGER.sub("", text)
    return _rewrite_css_urls(text, base, allow_remote=allow_remote, asset_url=asset_url)


def _rewrite_srcset(
    value: str, base: str, *, allow_remote: bool, asset_url: Callable[[str], str]
) -> str | None:
    parts: list[str] = []
    for candidate in value.split(","):
        bits = candidate.strip().split()
        if not bits:
            continue
        kind, ref = resolve_reference(bits[0], base, allow_remote=allow_remote)
        if kind in {"block", "remote"}:
            continue
        if kind == "internal":
            bits[0] = asset_url(ref)
        parts.append(" ".join(bits))
    return ", ".join(parts) if parts else None


def _sanitize_element(
    element,
    *,
    base: str,
    asset_url: Callable[[str], str],
    chapter_url: Callable[[str], str] | None,
    allow_remote: bool,
) -> None:
    tag = str(element.tag).lower() if isinstance(element.tag, str) else ""
    if tag in BLOCKED_TAGS:
        element.drop_tree()
        return
    if tag == "link":
        rel = (element.get("rel") or "").lower()
        href = element.get("href") or ""
        kind, value = resolve_reference(href, base, allow_remote=allow_remote)
        if "stylesheet" in rel and kind == "internal":
            element.set("href", asset_url(value))
        else:
            element.drop_tree()
        return

    for name in list(element.attrib):
        low = name.lower()
        if low.startswith("on") or low in {"srcdoc", "formaction"}:
            del element.attrib[name]
            continue
        if low == "style":
            element.set(name, sanitize_css(element.get(name) or "", base=base,
                                           asset_url=asset_url, allow_remote=allow_remote))
            continue
        if low not in URL_ATTRS and not low.endswith("}href"):
            continue
        raw = element.get(name) or ""
        if low == "srcset":
            rewritten = _rewrite_srcset(raw, base, allow_remote=allow_remote, asset_url=asset_url)
            if rewritten:
                element.set(name, rewritten)
            else:
                del element.attrib[name]
            continue
        kind, value = resolve_reference(raw, base, allow_remote=allow_remote)
        if kind == "keep":
            continue
        if kind == "remote":
            element.set(name, value)
            continue
        if kind == "block":
            del element.attrib[name]
            continue
        is_link = tag == "a" or tag == "area"
        if is_link and chapter_url is not None:
            path = value.split("#", 1)[0]
            element.set(name, chapter_url(path))
        else:
            element.set(name, asset_url(value))


def sanitize_html(
    raw: bytes | str,
    *,
    base: str,
    asset_url: Callable[[str], str],
    chapter_url: Callable[[str], str] | None = None,
    allow_remote: bool = False,
    title: str = "",
) -> str:
    """Return a full, safe HTML document for an iframe.

    ``base`` is the internal directory of the source document (used to resolve
    relative URLs). Raises :class:`ReaderError` when parsing fails.
    """
    if isinstance(raw, bytes):
        data = raw
    else:
        data = (raw or "<body></body>").encode("utf-8", errors="replace")

    try:
        doc = lxml_html.document_fromstring(data)
    except Exception:  # noqa: BLE001 - malformed input must never break the reader
        doc = lxml_html.document_fromstring(b"<body></body>")

    for element in list(doc.iter()):
        if not isinstance(element.tag, str):
            continue
        _sanitize_element(
            element,
            base=base,
            asset_url=asset_url,
            chapter_url=chapter_url,
            allow_remote=allow_remote,
        )

    head = doc.find("head")
    if head is None:
        head = lxml_html.Element("head")
        doc.insert(0, head)
    for stale in head.findall("style[@id='reader-base']"):
        head.remove(stale)
    style = lxml_html.Element("style")
    style.set("id", "reader-base")
    style.text = BASE_STYLE
    head.insert(0, style)
    if title:
        for existing in head.findall("title"):
            head.remove(existing)

    return lxml_html.tostring(doc, encoding="unicode", method="html")
