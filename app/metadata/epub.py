"""EPUB metadata + cover extraction (no external dependency)."""

from __future__ import annotations

import contextlib
import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

CONTAINER = "META-INF/container.xml"
NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
}


def _read_rootfile(zf: zipfile.ZipFile) -> str | None:
    try:
        data = zf.read(CONTAINER)
    except KeyError:
        # Fall back to the conventional location.
        for name in zf.namelist():
            if name.lower().endswith(".opf"):
                return name
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    for rootfile in root.iter():
        if rootfile.tag.endswith("rootfile"):
            return rootfile.attrib.get("full-path")
    return None


def read_epub_metadata(path: Path) -> dict:
    result: dict = {
        "title": None,
        "author": None,
        "language": None,
        "description": None,
        "publisher": None,
        "isbn": None,
        "series": None,
        "series_index": None,
        "page_count": 0,
        "has_fixed_layout": False,
        "image_only": False,
    }
    try:
        with zipfile.ZipFile(path) as zf:
            opf_path = _read_rootfile(zf)
            if not opf_path:
                return result
            root = ET.fromstring(zf.read(opf_path))
    except (zipfile.BadZipFile, OSError, ET.ParseError, KeyError):
        return result

    def dc(tag: str) -> str | None:
        el = root.find(f".//dc:{tag}", NS)
        if el is None:
            el = root.find(f".//{{http://purl.org/dc/elements/1.1/}}{tag}")
        return el.text.strip() if el is not None and el.text else None

    result["title"] = dc("title")
    result["author"] = dc("creator")
    result["language"] = dc("language")
    result["description"] = dc("description")
    result["publisher"] = dc("publisher")
    result["isbn"] = dc("identifier")

    # Calibre-style series metadata.
    for meta in root.iter():
        if not meta.tag.endswith("meta"):
            continue
        name = (meta.attrib.get("name") or "").lower()
        prop = (meta.attrib.get("property") or "").lower()
        value = meta.attrib.get("content") or (meta.text or "")
        value = value.strip() or None
        if name in {"calibre:series", "belongs-to-collection"} or prop == "belongs-to-collection":
            result["series"] = value
        elif name == "calibre:series_index" or prop == "group-position":
            with contextlib.suppress(TypeError, ValueError):
                result["series_index"] = float(value) if value else None
        elif prop in {"rendition:layout"} and value:
            result["has_fixed_layout"] = value.lower() == "pre-pub" or value.lower() == "pre"

    # Count spine items (approximate page count for EPUB) and spot image-only books.
    spine_refs: list[str] = []
    image_refs = 0
    for item in root.iter():
        if item.tag.endswith("item") and item.attrib.get("media-type", "").startswith("image/"):
            image_refs += 1
        if item.tag.endswith("itemref"):
            ref = item.attrib.get("idref")
            if ref:
                spine_refs.append(ref)
    result["page_count"] = len(spine_refs)
    result["image_only"] = image_refs > 5 and len(spine_refs) <= 1
    return result


def extract_epub_cover(path: Path) -> bytes | None:
    """Return cover image bytes, using OPF metadata then a filename heuristic."""
    try:
        with zipfile.ZipFile(path) as zf:
            opf_path = _read_rootfile(zf)
            if opf_path:
                root = ET.fromstring(zf.read(opf_path))
                opf_dir = posixpath.dirname(opf_path)
                # <meta name="cover" content="id"/>
                cover_id = None
                for meta in root.iter():
                    if meta.tag.endswith("meta") and (meta.attrib.get("name") == "cover"):
                        cover_id = meta.attrib.get("content")
                if cover_id:
                    for item in root.iter():
                        if item.tag.endswith("item") and item.attrib.get("id") == cover_id:
                            href = posixpath.normpath(posixpath.join(opf_dir, item.attrib["href"]))
                            try:
                                return zf.read(href)
                            except KeyError:
                                pass
                # EPUB3 properties="cover-image"
                for item in root.iter():
                    if item.tag.endswith("item") and "cover-image" in (item.attrib.get("properties") or ""):
                        href = posixpath.normpath(posixpath.join(opf_dir, item.attrib["href"]))
                        try:
                            return zf.read(href)
                        except KeyError:
                            pass
            # Heuristic: something named cover.*
            for name in zf.namelist():
                low = name.lower()
                if "cover" in low and low.rsplit(".", 1)[-1] in {"jpg", "jpeg", "png", "gif", "webp"}:
                    return zf.read(name)
    except (zipfile.BadZipFile, OSError, ET.ParseError, KeyError):
        return None
    return None
