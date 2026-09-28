"""Image helpers shared by the PDF, comic and image handlers.

Uses Pillow for bitmap work and PyMuPDF (already a project dependency) for PDF
page rendering. Everything here is synchronous and meant to run inside
``asyncio.to_thread`` from the routes.
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from app.library.formats import IMAGE_EXTS

#: Formats every current browser renders directly.
WEB_IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "gif", "webp", "bmp", "avif"})
#: Accepted but not natively displayed -> converted on the fly.
CONVERT_IMAGE_EXTS = frozenset(IMAGE_EXTS - WEB_IMAGE_EXTS)

_DEFAULT_LONG_SIDE = 1600
_MAX_LONG_SIDE = 4096


@lru_cache(maxsize=1)
def _pymupdf():
    try:
        import pymupdf  # type: ignore

        return pymupdf
    except ImportError:  # pragma: no cover - older installs
        import fitz  # type: ignore

        return fitz


def is_web_image(fmt: str) -> bool:
    return (fmt or "").lower().lstrip(".") in WEB_IMAGE_EXTS


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def pdf_page_count(source: Path) -> int:
    with _pymupdf().open(str(source)) as doc:
        return int(doc.page_count)


def render_pdf_page(
    source: Path,
    index: int,
    *,
    width: int = 0,
    gray: bool = False,
    quality: int = 88,
) -> tuple[bytes, str]:
    """Render one PDF page. Returns ``(data, mime)``."""
    mupdf = _pymupdf()
    with mupdf.open(str(source)) as doc:
        if index < 0 or index >= doc.page_count:
            raise IndexError(index)
        page = doc.load_page(index)
        longest = max(page.rect.width, page.rect.height) or 1.0
        if width:
            scale = _clamp(width, 120, _MAX_LONG_SIDE) / longest
        else:
            scale = _DEFAULT_LONG_SIDE / longest
        matrix = mupdf.Matrix(scale, scale)
        pix = page.get_pixmap(
            matrix=matrix,
            colorspace=mupdf.csGRAY if gray else mupdf.csRGB,
            alpha=False,
        )
        if gray:
            return pix.tobytes("png"), "image/png"
        return pix.tobytes("jpg", jpg_quality=quality), "image/jpeg"


def transform_image(
    data: bytes,
    *,
    width: int = 0,
    gray: bool = False,
    max_long_side: int = _DEFAULT_LONG_SIDE,
    quality: int = 88,
) -> tuple[bytes, str]:
    """Decode, optionally resize/grayscale and re-encode an image.

    Returns the original bytes untouched when no transformation is needed.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Imagem ilegível.") from exc

    if gray:
        image = image.convert("L")
    elif image.mode in {"P", "CMYK", "LA", "RGBA"}:
        image = image.convert("RGB")

    if width and width > 0:
        limit = _clamp(width, 80, _MAX_LONG_SIDE)
        longest = max(image.size) or 1
        if longest > limit or image.size[0] < limit // 2:
            ratio = limit / longest
            new_size = (max(1, round(image.size[0] * ratio)), max(1, round(image.size[1] * ratio)))
            image = image.resize(new_size, Image.Resampling.LANCZOS)
    elif max_long_side:
        longest = max(image.size) or 1
        if longest > max_long_side:
            ratio = max_long_side / longest
            image = image.resize(
                (max(1, round(image.size[0] * ratio)), max(1, round(image.size[1] * ratio))),
                Image.Resampling.LANCZOS,
            )

    buffer = io.BytesIO()
    if gray:
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue(), "image/png"
    if image.mode == "RGB":
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        return buffer.getvalue(), "image/jpeg"
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), "image/png"


def load_image(data: bytes):
    """Open an image for metadata inspection (size, format)."""
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    image.load()
    return image
