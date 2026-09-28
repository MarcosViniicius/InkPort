"""Cover generation for every supported input.

Always produces a normalised JPEG, so OPDS feeds and the panel have a usable
thumbnail even when the source has no embedded cover -- and a typographic one
when the book has no image at all (text EPUBs).

Heavy imports are done inside the functions on purpose: this module is imported
from ``app.library.importer``, and importing ``app.converters``/``app.library``
at module level would create an import cycle.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoids importing app.library at module import time
    from app.library.detection import Detection

logger = logging.getLogger(__name__)

MAX_SIDE = 700
COVER_QUALITY = 86

#: Typographic cover for books that have no embedded image (text EPUBs).
COVER_SIZE = (800, 1200)
COVER_PAPER = (246, 243, 237)
COVER_INK = (27, 25, 23)
COVER_MUTED = (111, 106, 98)
COVER_ACCENT = (31, 111, 92)

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\georgia.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)


def _load_font(size: int):
    from PIL import ImageFont

    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, AttributeError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def generate_title_cover(title: str, author: str | None, out_dir: Path) -> Path:
    """A clean typographic cover, used when the book has no cover image."""
    from PIL import Image, ImageDraw

    width, height = COVER_SIZE
    image = Image.new("RGB", COVER_SIZE, COVER_PAPER)
    draw = ImageDraw.Draw(image)

    margin = 72
    draw.rectangle([margin, margin, margin + 96, margin + 9], fill=COVER_ACCENT)

    title_font = _load_font(66)
    author_font = _load_font(30)

    max_width = width - margin * 2
    line_height = 82
    lines: list[str] = []
    for word in (title or "Sem título").split():
        if lines and draw.textlength(f"{lines[-1]} {word}", font=title_font) > max_width:
            lines.append(word)
        elif lines:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    lines = lines[:9]

    y = margin + 120
    for line in lines:
        draw.text((margin, y), line, font=title_font, fill=COVER_INK)
        y += line_height

    if author:
        draw.text((margin, height - margin - 60), author[:60], font=author_font, fill=COVER_MUTED)
    draw.rectangle([margin, height - margin - 3, width - margin, height - margin], fill=COVER_ACCENT)

    from app.library.hashing import hash_bytes

    out_dir.mkdir(parents=True, exist_ok=True)
    key = hash_bytes(f"{title}|{author}".encode())[:32]
    target = out_dir / f"{key}.jpg"
    image.save(target, format="JPEG", quality=COVER_QUALITY, optimize=True)
    return target


def generate_cover(
    source: Path,
    out_dir: Path,
    detection: Detection,
    *,
    title: str | None = None,
    author: str | None = None,
) -> Path | None:
    """Return a path to a generated JPEG cover, or ``None`` if impossible."""
    try:
        payload = _cover_payload(source, detection)
        if payload:
            return _store_jpeg(payload, out_dir)
    except Exception as exc:  # a missing cover must never fail an import
        logger.warning(
            "cover generation failed",
            extra={"path": str(source), "error": str(exc)},
        )
    if title:
        try:
            return generate_title_cover(title, author, out_dir)
        except Exception as exc:  # pragma: no cover
            logger.warning("title cover failed", extra={"error": str(exc)})
    return None


def _cover_payload(source: Path, detection: Detection) -> bytes | Path | None:
    fmt = detection.format
    if fmt == "epub":
        from app.metadata.epub import extract_epub_cover

        return extract_epub_cover(source)
    if fmt in {"mobi", "azw", "azw3"}:
        from app.metadata.mobi import extract_mobi_cover

        return extract_mobi_cover(source)
    if detection.is_image:
        return source
    if fmt in {"cbz", "zip"}:
        return _first_zip_image(source)
    if fmt == "pdf":
        return _pdf_first_page(source)
    if fmt in {"cbr", "rar", "cb7", "7z"}:
        return _archive_first_image(source, detection)
    return None


def _first_zip_image(source: Path) -> bytes | None:
    import zipfile

    from app.library.containers import zip_image_names

    names = zip_image_names(source)
    if not names:
        return None
    with zipfile.ZipFile(source) as zf:
        return zf.read(names[0])


def _archive_first_image(source: Path, detection: Detection) -> bytes | None:
    """Extract only the first image from a RAR/7z archive into a temp folder."""
    from app.converters.collectors import collect_pages
    from app.storage.temp import temp_workdir

    with temp_workdir("cover_") as workdir:
        pages = collect_pages(source, detection, workdir)
        if not pages:
            return None
        return pages[0].read_bytes()


def _pdf_first_page(source: Path) -> bytes | None:
    from app.metadata.pdf import extract_pdf_cover
    from app.storage.temp import temp_workdir

    with temp_workdir("cover_pdf_") as workdir:
        png = workdir / "cover.png"
        if extract_pdf_cover(source, png):
            return png.read_bytes()
    return None


def store_cover(data: bytes, out_dir: Path) -> Path:
    """Public helper: normalise arbitrary image bytes into a stored cover JPEG."""
    return _store_jpeg(data, out_dir)


def get_thumbnail(cover_filename: str, out_dir: Path, *, max_side: int = 200) -> Path | None:
    """Return a cached thumbnail for a stored cover (created on first request)."""
    from app.converters.imageops import make_thumbnail, open_image, save_image

    source = out_dir / Path(cover_filename).name
    if not source.exists():
        return None
    target = out_dir / f"thumb_{source.stem}_{max_side}.jpg"
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    try:
        img = open_image(source)
        thumb = make_thumbnail(img, max_side)
        if thumb.mode not in {"RGB", "L"}:
            thumb = thumb.convert("RGB")
        save_image(thumb, target, quality=80)
    except Exception as exc:  # pragma: no cover
        logger.warning("thumbnail failed", extra={"error": str(exc)})
        return source
    return target


def _store_jpeg(payload: bytes | Path, out_dir: Path) -> Path:
    from app.converters.imageops import make_thumbnail, open_image, save_image
    from app.library.hashing import hash_bytes

    data = payload.read_bytes() if isinstance(payload, Path) else payload
    img = open_image(data)
    thumb = make_thumbnail(img, MAX_SIDE)
    if thumb.mode not in {"RGB", "L"}:
        thumb = thumb.convert("RGB")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{hash_bytes(data)[:32]}.jpg"
    save_image(thumb, target, quality=COVER_QUALITY)
    return target
