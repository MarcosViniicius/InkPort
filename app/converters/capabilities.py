"""What this installation can do -- without asking anyone to install anything.

Everything the server needs ships as a Python package (or as the UnRAR library
we redistribute in ``app/vendor``). This module answers two questions:

* which *capabilities* are available (PDF rendering, archives, ebook formats);
* which optional accelerators happen to be present, so exotic inputs can still
  be handled when the machine already has them.

Converters never hard-fail because something is missing: they declare what they
need and the registry picks the best one that can actually run here.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field

from app.config import get_settings

logger = logging.getLogger(__name__)

#: Image formats Pillow (plus its plugins) can open.
_IMAGE_FORMATS = ("JPEG", "PNG", "GIF", "WEBP", "BMP", "TIFF", "AVIF", "HEIF")


def _module_version(name: str) -> str | None:
    try:
        module = importlib.import_module(name)
    except ImportError:
        return None
    return getattr(module, "__version__", "instalado")


def _pymupdf_version() -> str | None:
    for name in ("pymupdf", "fitz"):
        version = _module_version(name)
        if version:
            return version
    return None


def _pillow_features() -> list[str]:
    try:
        from PIL import features
    except ImportError:  # pragma: no cover
        return []
    available = ["JPEG", "PNG", "GIF", "WEBP", "BMP", "TIFF"]
    import warnings

    for name in ("AVIF", "HEIF"):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # Pillow warns about unknown features
                if features.check(name):
                    available.append(name)
        except Exception:  # noqa: BLE001 - a missing plugin is not an error
            continue
    return available


@dataclass(slots=True)
class Capabilities:
    """Resolved capabilities plus the optional extras found on this machine."""

    #: Python backends that are always installed with the project.
    backends: dict[str, str | None] = field(default_factory=dict)
    #: Optional accelerators (never required).
    optional: dict[str, str | None] = field(default_factory=dict)
    image_formats: list[str] = field(default_factory=list)
    archive_backends: dict[str, object] = field(default_factory=dict)

    # -- questions the rest of the app asks ---------------------------------
    def has_pdf(self) -> bool:
        return bool(self.backends.get("PyMuPDF"))

    def has_calibre(self) -> bool:
        """Calibre is optional now: only exotic formats still use it."""
        return bool(self.optional.get("Calibre"))

    def has_imagemagick(self) -> bool:
        return bool(self.optional.get("ImageMagick"))

    def has_text_enlarge(self) -> bool:
        """Manga/comic text enlargement works with Pillow alone."""
        return True

    def has_ocr(self) -> bool:
        """Optional OCR extra (RapidOCR/ONNX), used by the precise modes."""
        from app.converters.manga import ocr

        try:
            return ocr.available()
        except Exception:  # noqa: BLE001 - a missing extra is not an error
            return False

    def image_renderer(self) -> str | None:
        """PDF -> image renderer name (PyMuPDF always wins)."""
        return "pymupdf" if self.has_pdf() else None

    def has_rar(self) -> bool:
        return bool(self.archive_backends.get("rar"))

    def as_dict(self) -> dict:
        """Flat, human-readable map for the panel: label -> version or None."""
        report: dict[str, str | None] = {}
        report["PyMuPDF (PDF: reflow, imagens, escrever PDF)"] = self.backends.get("PyMuPDF")
        pillow = self.backends.get("Pillow")
        report["Pillow (imagens: redimensionar, recortar, tons de cinza)"] = (
            f"{pillow} — {', '.join(self.image_formats)}" if pillow else None
        )
        report["py7zr (7z / CB7)"] = self.backends.get("py7zr")
        report["rarfile (RAR / CBR)"] = self.backends.get("rarfile")
        report["lxml (HTML/XHTML e EPUB)"] = self.backends.get("lxml")
        report["pypdf (metadados de PDF)"] = self.backends.get("pypdf")
        report["python-docx (DOCX)"] = self.backends.get("docx")
        report["striprtf (RTF)"] = self.backends.get("striprtf")
        report["mobi (ler MOBI/AZW)"] = self.backends.get("mobi")
        report["UnRAR embutido (CBR/RAR completo)"] = (
            "embutida em app/vendor/unrar" if self.archive_backends.get("rar") else None
        )
        report["Calibre (opcional, só formatos exóticos)"] = self.optional.get("Calibre")
        report["Ampliação de texto (mangá/quadrinhos)"] = (
            "motor local (Pillow, sem IA e sem internet)" if self.has_text_enlarge() else None
        )
        report["OCR de mangá (modo preciso, opcional)"] = (
            "RapidOCR / PP-OCRv6 (ONNX, CPU)" if self.has_ocr() else None
        )
        report["IA para decisões (opcional)"] = None
        return report


_cached: Capabilities | None = None


def detect_toolchain(refresh: bool = False) -> Capabilities:  # noqa: N802 - kept name
    """Kept as ``detect_toolchain`` so existing callers keep working."""
    global _cached
    if _cached is not None and not refresh:
        return _cached

    settings = get_settings()
    capabilities = Capabilities()

    capabilities.backends = {
        "PyMuPDF": _pymupdf_version(),
        "Pillow": _module_version("PIL"),
        "py7zr": _module_version("py7zr"),
        "rarfile": _module_version("rarfile"),
        "lxml": _module_version("lxml"),
        "pypdf": _module_version("pypdf"),
        "docx": _module_version("docx"),
        "striprtf": _module_version("striprtf"),
        "mobi": _module_version("mobi"),
    }
    capabilities.image_formats = _pillow_features()

    from app.converters.archives import available_backends

    try:
        capabilities.archive_backends = available_backends()
    except Exception as exc:  # noqa: BLE001 - the panel must render regardless
        logger.warning("archive backend probe failed", extra={"error": str(exc)})
        capabilities.archive_backends = {}

    from app.converters.tools_optional import find_optional_tools

    capabilities.optional = find_optional_tools(settings)

    logger.info(
        "conversion capabilities resolved",
        extra={
            "backends": {k: bool(v) for k, v in capabilities.backends.items()},
            "optional": {k: bool(v) for k, v in capabilities.optional.items()},
            "archives": capabilities.archive_backends,
        },
    )
    _cached = capabilities
    return capabilities
