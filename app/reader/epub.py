"""EPUB (and KEPUB) handler.

Maximum fidelity: nothing is converted. The book's own XHTML is served inside a
sandboxed iframe, its own stylesheets and images are served through the asset
route, and the spine/TOC drive navigation. Content is sanitised on the way out.
"""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from starlette.responses import HTMLResponse, Response

from app.library.formats import mime_for
from app.reader.base import ReaderContext, ReaderError, ReaderHandler, safe_join
from app.reader.sanitize import (
    asset_url_factory,
    chapter_url_factory,
    sanitize_css,
    sanitize_html,
)

CONTAINER = "META-INF/container.xml"
_OPF_NS = "http://www.idpf.org/2007/opf"
_XHTML_MIME = "application/xhtml+xml"

_EXTRA_MIME = {
    "css": "text/css",
    "svg": "image/svg+xml",
    "ttf": "font/ttf",
    "otf": "font/otf",
    "woff": "font/woff",
    "woff2": "font/woff2",
    "js": "application/javascript",
    "ncx": "application/x-dtbncx+xml",
    "xhtml": _XHTML_MIME,
    "html": "text/html",
    "xml": "application/xml",
    "json": "application/json",
}


@dataclass(slots=True)
class EpubStructure:
    opf_path: str
    opf_dir: str
    media_types: dict[str, str] = field(default_factory=dict)
    spine: list[str] = field(default_factory=list)
    toc: list[dict] = field(default_factory=list)
    nav_href: str | None = None
    ncx_href: str | None = None
    title: str = ""


class EpubHandler(ReaderHandler):
    name = "epub"
    label = "EPUB"
    formats = frozenset({"epub", "kepub"})

    # -- manifest ----------------------------------------------------------
    def capabilities(self, ctx: ReaderContext) -> dict:
        return {"native": True, "pages": False, "chapters": True, "assets": True, "audio": False}

    def manifest(self, ctx: ReaderContext) -> dict:
        structure = self._structure(ctx.path)
        chapters = self._chapters(structure)
        from app.reader.manifest import build_manifest

        return build_manifest(
            ctx,
            handler=self,
            capabilities=self.capabilities(ctx),
            chapters=chapters,
            page_count=len(structure.spine) or ctx.detection.page_count or 0,
            extra={"title": structure.title},
        )

    # -- content -----------------------------------------------------------
    def chapter(self, ctx: ReaderContext, key: str) -> Response:
        internal = safe_join("", unquote(key))
        try:
            with zipfile.ZipFile(ctx.path) as archive:
                data = archive.read(internal)
        except (KeyError, zipfile.BadZipFile, OSError) as exc:
            raise ReaderError("Capítulo não encontrado no EPUB.", status_code=404) from exc
        base = posixpath.dirname(internal)
        body = sanitize_html(
            data,
            base=base,
            asset_url=asset_url_factory(ctx.book_id),
            chapter_url=chapter_url_factory(ctx.book_id),
            title=self._chapter_title(ctx.path, internal),
        )
        return HTMLResponse(body, headers=_no_cache_headers())

    def asset(self, ctx: ReaderContext, key: str) -> Response:
        internal = safe_join("", unquote(key))
        try:
            with zipfile.ZipFile(ctx.path) as archive:
                data = archive.read(internal)
        except (KeyError, zipfile.BadZipFile, OSError) as exc:
            raise ReaderError("Recurso não encontrado no EPUB.", status_code=404) from exc

        mime = self._mime_for(ctx.path, internal)
        if mime in {"text/css", "text/x-css"} or internal.lower().endswith(".css"):
            text = data.decode("utf-8", errors="replace")
            cleaned = sanitize_css(
                text,
                base=posixpath.dirname(internal),
                asset_url=asset_url_factory(ctx.book_id),
            )
            return Response(cleaned, media_type="text/css", headers=_no_cache_headers())
        return Response(data, media_type=mime, headers=_no_cache_headers())

    # -- parsing -----------------------------------------------------------
    def _structure(self, path: Path) -> EpubStructure:
        try:
            with zipfile.ZipFile(path) as archive:
                opf_path = _rootfile(archive)
                if not opf_path:
                    raise ReaderError("EPUB sem OPF (estrutura inválida).")
                try:
                    opf_bytes = archive.read(opf_path)
                except KeyError as exc:
                    raise ReaderError("EPUB sem OPF (estrutura inválida).") from exc
                namespace = {"opf": _OPF_NS, "dc": "http://purl.org/dc/elements/1.1/"}
                root = ET.fromstring(opf_bytes)
                opf_dir = posixpath.dirname(opf_path)
                media_types, nav_href, ncx_href, title = _read_manifest(root, opf_dir, namespace)
                spine = _read_spine(root, opf_dir, media_types)
                toc = _read_toc(archive, nav_href, ncx_href, media_types)
        except zipfile.BadZipFile as exc:
            raise ReaderError("Arquivo EPUB corrompido.") from exc
        return EpubStructure(
            opf_path=opf_path,
            opf_dir=opf_dir,
            media_types=media_types,
            spine=spine,
            toc=toc,
            nav_href=nav_href,
            ncx_href=ncx_href,
            title=title,
        )

    def _chapters(self, structure: EpubStructure) -> list[dict]:
        titles: dict[str, str] = {}
        for item in structure.toc:
            titles.setdefault(item["key"].split("#", 1)[0], item["title"])
        chapters: list[dict] = []
        for index, href in enumerate(structure.spine):
            chapters.append(
                {
                    "key": href,
                    "title": titles.get(href) or f"Parte {index + 1}",
                    "index": index,
                }
            )
        return chapters

    def _chapter_title(self, path: Path, internal: str) -> str:
        try:
            structure = self._structure(path)
        except ReaderError:
            return ""
        for item in structure.toc:
            if item["key"].split("#", 1)[0] == internal:
                return item["title"]
        return ""

    def _mime_for(self, path: Path, internal: str) -> str:
        try:
            structure = self._structure(path)
            if internal in structure.media_types:
                return structure.media_types[internal]
        except ReaderError:
            pass
        ext = internal.rsplit(".", 1)[-1].lower()
        return _EXTRA_MIME.get(ext) or mime_for(ext) or "application/octet-stream"


def _rootfile(archive: zipfile.ZipFile) -> str | None:
    try:
        data = archive.read(CONTAINER)
    except KeyError:
        for name in archive.namelist():
            if name.lower().endswith(".opf"):
                return name
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    for element in root.iter():
        if element.tag.endswith("rootfile"):
            return element.attrib.get("full-path")
    return None


def _read_manifest(root, opf_dir: str, namespace: dict):
    media_types: dict[str, str] = {}
    nav_href: str | None = None
    ncx_href: str | None = None
    for item in root.iter():
        if not item.tag.endswith("item"):
            continue
        href = item.attrib.get("href")
        if not href:
            continue
        full = posixpath.normpath(posixpath.join(opf_dir, href))
        mime = item.attrib.get("media-type", "")
        if mime:
            media_types[full] = mime
        if "nav" in (item.attrib.get("properties") or "").split():
            nav_href = full
        if mime == "application/x-dtbncx+xml":
            ncx_href = full
    title = ""
    for element in root.iter():
        if element.tag.endswith("title") and element.text:
            title = element.text.strip()
            break
    return media_types, nav_href, ncx_href, title


def _read_spine(root, opf_dir: str, media_types: dict[str, str]) -> list[str]:
    refs: list[str] = []
    ids: dict[str, str] = {}
    for item in root.iter():
        if item.tag.endswith("item") and item.attrib.get("id") and item.attrib.get("href"):
            ids[item.attrib["id"]] = posixpath.normpath(
                posixpath.join(opf_dir, item.attrib["href"])
            )
    for itemref in root.iter():
        if not itemref.tag.endswith("itemref"):
            continue
        ref = itemref.attrib.get("idref")
        if ref and ref in ids:
            refs.append(ids[ref])
    if refs:
        return refs
    # No usable spine: fall back to the reading order of XHTML items.
    return [href for href, mime in media_types.items() if mime == _XHTML_MIME]


def _read_toc(
    archive: zipfile.ZipFile, nav_href: str | None, ncx_href: str | None,
    media_types: dict[str, str],
) -> list[dict]:
    if nav_href:
        entry = _read_nav(archive, nav_href)
        if entry:
            return entry
    if ncx_href:
        entry = _read_ncx(archive, ncx_href)
        if entry:
            return entry
    return []


def _read_nav(archive: zipfile.ZipFile, nav_href: str) -> list[dict]:
    try:
        raw = archive.read(nav_href)
        root = ET.fromstring(raw)
    except (KeyError, ET.ParseError, OSError):
        return []
    base = posixpath.dirname(nav_href)
    entries: list[dict] = []
    for nav in root.iter():
        if not nav.tag.endswith("nav"):
            continue
        if "toc" not in (nav.attrib.get("{http://www.idpf.org/2007/ops}type") or ""):
            continue
        for depth, anchor in _walk_nav(nav):
            href = anchor.attrib.get("href")
            if not href:
                continue
            key = posixpath.normpath(posixpath.join(base, href))
            entries.append({"key": key, "title": _text(anchor) or key, "level": depth})
        if entries:
            return entries
    return entries


def _walk_nav(element, depth: int = 0):
    for child in element:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "a":
            yield depth, child
        elif tag in {"ol", "ul"}:
            yield from _walk_nav(child, depth + 1)
        elif tag == "li":
            yield from _walk_nav(child, depth)


def _read_ncx(archive: zipfile.ZipFile, ncx_href: str) -> list[dict]:
    try:
        raw = archive.read(ncx_href)
        root = ET.fromstring(raw)
    except (KeyError, ET.ParseError, OSError):
        return []
    base = posixpath.dirname(ncx_href)
    entries: list[dict] = []
    for point in root.iter():
        if not point.tag.endswith("navPoint"):
            continue
        label = ""
        source = ""
        for child in point.iter():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "text" and child.text and not label:
                label = child.text.strip()
            elif tag == "content" and child.attrib.get("src"):
                source = child.attrib["src"]
        if not source:
            continue
        key = posixpath.normpath(posixpath.join(base, source))
        entries.append({"key": key, "title": label or key, "level": 0})
    return entries


def _text(element) -> str:
    return "".join(element.itertext()).strip().replace("\n", " ")


def _no_cache_headers() -> dict[str, str]:
    return {"Cache-Control": "private, max-age=3600"}
