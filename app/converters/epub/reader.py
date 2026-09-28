"""Reading an EPUB into memory: manifest, spine order and resources.

Used by the converters that *produce* another format from an EPUB (KEPUB, TXT,
FB2, DOCX, PDF). It is deliberately small: enough structure to walk the spine
in order with the resources each document needs.
"""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

CONTAINER = "META-INF/container.xml"
OPF_NS = "{http://www.idpf.org/2007/opf}"
DC_NS = "{http://purl.org/dc/elements/1.1/}"


class EpubReadError(RuntimeError):
    """Raised when an EPUB cannot be read."""


@dataclass(slots=True)
class EpubItem:
    name: str          # path inside the zip, POSIX separators
    data: bytes = b""
    media_type: str = ""

    @property
    def is_image(self) -> bool:
        return self.media_type.startswith("image/") or _ext(self.name) in {
            "jpg", "jpeg", "png", "gif", "svg", "webp", "bmp", "avif", "tif", "tiff",
        }

    @property
    def is_style(self) -> bool:
        return self.media_type == "text/css" or _ext(self.name) == "css"

    @property
    def is_document(self) -> bool:
        return self.media_type in {"application/xhtml+xml", "text/html"} or _ext(
            self.name
        ) in {"xhtml", "html", "htm"}

    def text(self) -> str:
        return self.data.decode("utf-8", "replace")


@dataclass(slots=True)
class EpubDocument:
    title: str = ""
    author: str = ""
    language: str = ""
    publisher: str = ""
    description: str = ""
    identifier: str = ""
    items: dict[str, EpubItem] = field(default_factory=dict)
    spine: list[str] = field(default_factory=list)
    cover: str | None = None

    def documents(self) -> list[EpubItem]:
        """Spine documents in reading order (falls back to every document)."""
        ordered = [self.items[name] for name in self.spine if name in self.items]
        if ordered:
            return ordered
        return [item for item in self.items.values() if item.is_document]

    def images(self) -> list[EpubItem]:
        return [item for item in self.items.values() if item.is_image]

    def styles(self) -> list[EpubItem]:
        return [item for item in self.items.values() if item.is_style]

    def resolve(self, base: str, href: str) -> str:
        """Resolve ``href`` relative to the document ``base`` (zip paths)."""
        if not href:
            return ""
        href = href.split("#", 1)[0]
        if href.startswith("/"):
            return href.lstrip("/")
        return posixpath.normpath(posixpath.join(posixpath.dirname(base), href))


def read_epub(path: Path, *, include_images: bool = False) -> EpubDocument:
    """Parse an EPUB: metadata, manifest, spine and resources.

    Only text resources (XHTML/CSS) are read into memory by default; images can
    weigh hundreds of megabytes in a comic EPUB, so they are read on request
    (``include_images=True``) or streamed straight from the source zip.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            opf_name = _opf_path(zf, names)
            if opf_name is None:
                raise EpubReadError("EPUB sem OPF (META-INF/container.xml ausente).")
            root = ET.fromstring(zf.read(opf_name))
            document = _parse_opf(root, opf_name)
            _apply_media_types(root, opf_name, document)
            for name in zf.namelist():
                if name.endswith("/") or name == opf_name:
                    continue
                item = document.items.get(name) or EpubItem(name=name)
                if include_images or _looks_textual(name, item.media_type):
                    item.data = zf.read(name)
                document.items[name] = item
    except zipfile.BadZipFile as exc:
        raise EpubReadError("Arquivo EPUB inválido.") from exc
    except ET.ParseError as exc:
        raise EpubReadError(f"OPF ilegível: {exc}") from exc
    if not document.items:
        raise EpubReadError("EPUB vazio.")
    return document


def _looks_textual(name: str, media_type: str) -> bool:
    if media_type:
        return (
            media_type.startswith("text/")
            or media_type in {"application/xhtml+xml", "image/svg+xml"}
        )
    return _ext(name) in {"xhtml", "html", "htm", "css", "ncx", "opf", "xml", "svg", "txt"}


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _opf_path(zf: zipfile.ZipFile, names: set[str]) -> str | None:
    if CONTAINER in names:
        try:
            root = ET.fromstring(zf.read(CONTAINER))
        except ET.ParseError:
            root = None
        if root is not None:
            for element in root.iter():
                if element.tag.endswith("rootfile"):
                    candidate = element.get("full-path")
                    if candidate:
                        return candidate
    for name in names:
        if name.lower().endswith(".opf"):
            return name
    return None


def _parse_opf(root: ET.Element, opf_name: str) -> EpubDocument:
    document = EpubDocument()
    metadata = root.find(f"{OPF_NS}metadata") or root.find("metadata")
    if metadata is not None:
        values = {
            "title": ("title",),
            "author": ("creator",),
            "language": ("language",),
            "publisher": ("publisher",),
            "description": ("description",),
            "identifier": ("identifier",),
        }
        for attribute, tags in values.items():
            for tag in tags:
                element = metadata.find(f"{DC_NS}{tag}")
                if element is not None and (element.text or "").strip():
                    setattr(document, attribute, element.text.strip())
                    break

    manifest = root.find(f"{OPF_NS}manifest") or root.find("manifest")
    if manifest is not None:
        for item in manifest:
            if (item.get("properties") or "").find("cover-image") >= 0:
                document.cover = item.get("href")

    spine = root.find(f"{OPF_NS}spine") or root.find("spine")
    if spine is not None:
        for reference in spine:
            href = reference.get("href")
            if href:
                document.spine.append(posixpath.normpath(
                    posixpath.join(posixpath.dirname(opf_name), href)
                ))
    return document


def _apply_media_types(root: ET.Element, opf_name: str, document: EpubDocument) -> None:
    manifest = root.find(f"{OPF_NS}manifest") or root.find("manifest")
    if manifest is None:
        return
    base = posixpath.dirname(opf_name)
    for item in manifest:
        href = item.get("href")
        if not href:
            continue
        name = posixpath.normpath(posixpath.join(base, href))
        stored = document.items.get(name)
        if stored is not None:
            stored.media_type = item.get("media-type") or ""
