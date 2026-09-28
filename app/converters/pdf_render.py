"""PDF page extraction/rendering.

Quality-first, KCC-style:

1. If a page is a single embedded image covering the page (the common case for
   scanned manga), the **original image is extracted** — no re-render, no blurs.
2. Otherwise the page is rendered at a DPI computed from the *target* size
   (with supersampling), not a fixed low DPI, and written losslessly.
3. Grayscale rendering is used directly when the profile is grayscale, which
   costs less memory and avoids a later colour conversion.

Rendering is done by **PyMuPDF**, which ships as a pip wheel (bundled MuPDF):
no poppler, no Ghostscript, nothing to install on the machine.
"""

from __future__ import annotations

from pathlib import Path

#: Render resolution bounds.
MIN_DPI = 110
MAX_DPI = 320
SUPERSAMPLE = 1.6  # render this much larger than the final target, then downscale


class NoRendererError(RuntimeError):
    pass


def render_pdf_pages(
    source: Path,
    dest: Path,
    *,
    target: tuple[int, int] = (0, 0),
    dpi: int = 0,
    grayscale: bool = False,
    max_long_side: int = 0,
) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    try:
        return _with_pymupdf(
            source, dest, target=target, dpi=dpi,
            grayscale=grayscale, max_long_side=max_long_side,
        )
    except ImportError as exc:  # pragma: no cover - PyMuPDF is a requirement
        raise NoRendererError(
            "O renderizador de PDF (PyMuPDF) não está instalado neste Python. "
            "Ele vem como dependência do projeto: pip install PyMuPDF"
        ) from exc


# ---------------------------------------------------------------------------
# PyMuPDF (preferred)
# ---------------------------------------------------------------------------
def _with_pymupdf(
    source: Path,
    dest: Path,
    *,
    target: tuple[int, int],
    dpi: int,
    grayscale: bool,
    max_long_side: int,
) -> list[Path]:
    import pymupdf

    out: list[Path] = []
    with pymupdf.open(str(source)) as doc:
        for index in range(doc.page_count):
            page = doc.load_page(index)
            number = index + 1

            extracted = _extract_page_image(doc, page, dest, number)
            if extracted is not None:
                out.append(extracted)
                continue

            page_dpi = dpi or _auto_dpi(page.rect, target, max_long_side)
            matrix = pymupdf.Matrix(page_dpi / 72, page_dpi / 72)
            pix = page.get_pixmap(
                matrix=matrix,
                colorspace=pymupdf.csGRAY if grayscale else pymupdf.csRGB,
                alpha=False,
            )
            target_path = dest / f"{number:05d}.png"
            pix.save(str(target_path))
            out.append(target_path)
    return out


def _extract_page_image(doc, page, dest: Path, number: int) -> Path | None:
    """Return the page's single embedded image if it covers (almost) the page."""
    try:
        images = page.get_images(full=True)
    except Exception:
        return None
    if len(images) != 1:
        return None

    xref = images[0][0]
    try:
        rects = page.get_image_rects(xref)
    except Exception:
        return None
    if not rects:
        return None

    rect = rects[0]
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return None
    coverage = (rect.width * rect.height) / page_area
    if coverage < 0.85:
        return None

    try:
        info = doc.extract_image(xref)
    except Exception:
        return None
    data = info.get("image")
    if not data:
        return None

    ext = (info.get("ext") or "png").lower()
    ext = {"jpeg": "jpg", "jpe": "jpg"}.get(ext, ext)
    if ext not in {"jpg", "png", "webp", "bmp", "tif", "tiff"}:
        ext = "png"
    target_path = dest / f"{number:05d}.{ext}"
    target_path.write_bytes(data)
    return target_path


def _auto_dpi(rect, target: tuple[int, int], max_long_side: int) -> int:
    """Pick a DPI so the rendered long side matches what we will keep."""
    page_long = max(rect.width, rect.height)
    if page_long <= 0:
        return 200

    target_w, target_h = target
    if target_w and target_h:
        desired_long = max(target_w, target_h) * SUPERSAMPLE
    elif max_long_side:
        desired_long = max_long_side
    else:
        desired_long = 1600.0

    dpi = 72.0 * desired_long / page_long
    return int(max(MIN_DPI, min(MAX_DPI, round(dpi))))


def _fallback_dpi(target: tuple[int, int], max_long_side: int) -> int:
    target_w, target_h = target
    if target_w and target_h:
        return int(max(MIN_DPI, min(MAX_DPI, max(target_w, target_h) * SUPERSAMPLE / 8)))
    return 200 if not max_long_side else 240
