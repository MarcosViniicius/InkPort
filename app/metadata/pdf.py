"""PDF metadata, page count and cover extraction.

``pypdf`` handles metadata/text; rendering the first page to an image needs a
renderer. We try PyMuPDF (best), then ``pdftoppm`` (poppler), then Ghostscript,
and finally fall back to extracting an embedded image with pypdf.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def read_pdf_metadata(path: Path) -> dict:
    result = {
        "title": None,
        "author": None,
        "subject": None,
        "page_count": 0,
        "image_only_pages": False,
        "encrypted": False,
    }
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        return result

    try:
        reader = PdfReader(str(path))
        result["encrypted"] = bool(reader.is_encrypted)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return result
        result["page_count"] = len(reader.pages)
        meta = reader.metadata or {}
        result["title"] = (meta.get("/Title") or None)
        result["author"] = (meta.get("/Author") or None)
        result["subject"] = (meta.get("/Subject") or None)

        # Sample a handful of pages: if they carry almost no text, it is a scan.
        sample = min(len(reader.pages), 5)
        total_chars = 0
        for index in range(sample):
            try:
                total_chars += len((reader.pages[index].extract_text() or "").strip())
            except Exception:
                continue
        if sample and total_chars < 40 * sample:
            result["image_only_pages"] = True
    except Exception:
        pass
    return result


def _render_with_fitz(path: Path, out_png: Path, *, page: int = 0, dpi: int = 150) -> bool:
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # older PyMuPDF
        except ImportError:
            return False
    try:
        with fitz.open(str(path)) as doc:
            if page >= doc.page_count:
                return False
            pix = doc.load_page(page).get_pixmap(dpi=dpi)
            pix.save(str(out_png))
        return True
    except Exception:
        return False


def _render_with_pdftoppm(path: Path, out_png: Path, *, dpi: int = 150) -> bool:
    exe = shutil.which("pdftoppm")
    if not exe:
        return False
    try:
        out_base = out_png.with_suffix("")
        subprocess.run(
            [exe, "-png", "-r", str(dpi), "-f", "1", "-l", "1", str(path), str(out_base)],
            check=True, capture_output=True, timeout=120,
        )
        produced = list(out_png.parent.glob(out_base.name + "*"))
        if produced:
            produced[0].replace(out_png)
            return True
    except Exception:
        return False
    return False


def _render_with_ghostscript(path: Path, out_png: Path, *, dpi: int = 150) -> bool:
    for exe in ("gswin64c", "gswin32c", "gs"):
        found = shutil.which(exe)
        if found:
            break
    else:
        return False
    try:
        subprocess.run(
            [
                found, "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=png16m",
                f"-r{dpi}", "-dFirstPage=1", "-dLastPage=1",
                f"-sOutputFile={out_png}", str(path),
            ],
            check=True, capture_output=True, timeout=180,
        )
        return out_png.exists()
    except Exception:
        return False


def extract_pdf_cover(path: Path, out_png: Path, *, dpi: int = 150) -> bool:
    """Best-effort: render page 1 to ``out_png``."""
    out_png.parent.mkdir(parents=True, exist_ok=True)
    if _render_with_fitz(path, out_png, dpi=dpi):
        return True
    if _render_with_pdftoppm(path, out_png, dpi=dpi):
        return True
    if _render_with_ghostscript(path, out_png, dpi=dpi):
        return True
    # Last resort: pull an embedded image out of page 1.
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if reader.pages:
            for image in reader.pages[0].images:
                out_png.write_bytes(image.data)
                if out_png.stat().st_size > 0:
                    return True
    except Exception:
        return False
    return False
