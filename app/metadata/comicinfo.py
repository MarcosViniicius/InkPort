"""ComicInfo.xml parsing (the de-facto metadata standard for CBZ files)."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


def read_comicinfo(path: Path) -> dict | None:
    """Read ``ComicInfo.xml`` from a CBZ/ZIP archive, if present."""
    try:
        with zipfile.ZipFile(path) as zf:
            target = next(
                (n for n in zf.namelist() if n.lower().endswith("comicinfo.xml")), None
            )
            if not target:
                return None
            root = ET.fromstring(zf.read(target))
    except (zipfile.BadZipFile, OSError, ET.ParseError, KeyError):
        return None
    return _parse(root)


def _parse(root: ET.Element) -> dict:
    def text(tag: str) -> str | None:
        element = root.find(tag)
        return element.text.strip() if element is not None and element.text else None

    def number(tag: str) -> float | None:
        raw = text(tag)
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    manga = (text("Manga") or "").lower()
    reading_direction = "rtl" if manga in {"yes", "yesandrighttoleft"} else None

    return {
        "title": text("Title") or text("Series"),
        "series": text("Series"),
        "series_index": number("Number"),
        "author": text("Writer") or text("Penciller"),
        "publisher": text("Publisher"),
        "language": text("LanguageISO"),
        "description": text("Summary"),
        "page_count": number("PageCount"),
        "reading_direction": reading_direction,
        "age_rating": text("AgeRating"),
        "genre": text("Genre"),
    }
