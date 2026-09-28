"""Turning any comic-ish input into an ordered list of page images."""

from __future__ import annotations

import logging
from pathlib import Path

from app.converters.archives import extract_images
from app.library import formats
from app.library.containers import extract_zip_images, natural_key
from app.library.detection import Detection

logger = logging.getLogger(__name__)


def collect_pages(
    source: Path,
    detection: Detection,
    workdir: Path,
    *,
    target: tuple[int, int] = (0, 0),
    dpi: int = 0,
    grayscale: bool = False,
    max_long_side: int = 0,
) -> list[Path]:
    """Return the ordered source page images for an image/comic/document input.

    ``target``/``dpi``/``grayscale`` are used only by PDF rendering, to extract
    at the right resolution instead of a fixed low DPI.
    """
    fmt = detection.format

    if fmt in {"cbz", "zip"}:
        return extract_zip_images(source, workdir / "pages")
    if fmt in {"cbr", "rar", "cb7", "7z", "cbt"}:
        # Pure Python (zip/tar/7z) or the rarfile backend; see archives.py.
        return extract_images(source, workdir / "pages")
    if fmt == "pdf":
        from app.converters.pdf_render import render_pdf_pages

        return render_pdf_pages(
            source,
            workdir / "pages",
            target=target,
            dpi=dpi,
            grayscale=grayscale,
            max_long_side=max_long_side,
        )
    if fmt == "epub":
        return extract_epub_images(source, workdir / "pages")
    if fmt in formats.IMAGE_EXTS:
        return [source]

    raise ValueError(f"Formato de entrada sem páginas: {fmt}")


def extract_epub_images(source: Path, dest: Path) -> list[Path]:
    """Pull the image resources out of an EPUB (used for re-optimisation)."""
    import shutil
    import zipfile

    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    with zipfile.ZipFile(source) as zf:
        names = [
            n for n in zf.namelist()
            if not n.endswith("/") and n.rsplit(".", 1)[-1].lower() in formats.IMAGE_EXTS
        ]
        for index, name in enumerate(sorted(names, key=natural_key), start=1):
            ext = name.rsplit(".", 1)[-1].lower()
            target = dest / f"{index:05d}.{ext}"
            with zf.open(name) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            out.append(target)
    return out
