"""EPUB metadata value object and package constants."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

MIMETYPE = "application/epub+zip"

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

STYLES_CSS = """html, body { margin: 0; padding: 0; height: 100%; }
body { text-align: center; background: #ffffff; }
.page { margin: 0; padding: 0; page-break-after: always; }
.page img { max-width: 100%; max-height: 100%; height: auto; width: auto; }
.cover { margin: 0; padding: 0; text-align: center; }
.cover img { max-width: 100%; max-height: 100%; }
h1.book-title { font-family: serif; font-size: 1.4em; margin: 2em 0 0.4em; }
p.book-author { font-family: serif; font-size: 1em; color: #333; }
"""

_MIME_BY_EXT = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "svg": "image/svg+xml",
}


@dataclass(slots=True)
class EpubMeta:
    title: str = "Sem título"
    author: str | None = None
    language: str = "pt"
    identifier: str = field(default_factory=lambda: f"urn:uuid:{uuid.uuid4()}")
    publisher: str | None = None
    description: str | None = None
    series: str | None = None
    series_index: float | None = None
    subjects: list[str] = field(default_factory=list)
    reading_direction: str = "ltr"
    cover_image: Path | None = None
    fixed_layout: bool = False
    generator: str = "inkport"
    #: Publication date as text (``YYYY-MM-DD`` or a full timestamp).
    date: str | None = None


def mime_for(href: str) -> str:
    ext = href.rsplit(".", 1)[-1].lower()
    return _MIME_BY_EXT.get(ext, "application/octet-stream")
