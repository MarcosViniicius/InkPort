"""The conversion output catalog: labels and which targets suit an input."""

from __future__ import annotations

from app.converters.tools import detect_toolchain
from app.devices.profile import DeviceProfile
from app.library.detection import Detection

OUTPUT_LABELS = {
    "epub": "EPUB (reflowável ou imagens)",
    "epub_optimized": "EPUB otimizado para o dispositivo",
    "cbz": "CBZ (arquivo de quadrinhos)",
    "cbr": "CBR (arquivo de quadrinhos RAR)",
    "pdf": "PDF (layout fixo)",
    "mobi": "MOBI (Kindle antigo)",
    "azw3": "AZW3 (Kindle)",
    "kepub": "KEPUB (Kobo)",
    "docx": "DOCX (Word)",
    "fb2": "FB2 (FictionBook)",
    "txt": "TXT (texto puro)",
    "jpg": "Imagem JPG otimizada",
    "png": "Imagem PNG otimizada",
}

#: Formats produced by the pure-Python ebook backends from an EPUB source.
_FROM_EPUB = ("kepub", "azw3", "mobi", "pdf", "docx", "fb2", "txt")
#: Formats produced by the Pillow page pipeline.
_PAGE_FORMATS = {"epub", "cbz", "pdf"}
#: Inputs handled by the webpage pipeline (webpagetoepub logic).
_WEB_FORMATS = {"html", "htm", "xhtml"}
#: Text-like inputs the native readers understand.
_TEXT_INPUTS = {"txt", "fb2", "docx", "rtf", "mobi", "azw", "azw3", "pdb", "prc"}


def targets_for(detection: Detection, profile: DeviceProfile | None) -> list[dict]:
    """Ordered, annotated list of sensible conversion targets for this input."""
    chain = detect_toolchain()
    candidates: list[str] = []

    if detection.is_image:
        candidates = ["jpg", "png", "cbz", "epub"]
    elif detection.pages or detection.is_comic:
        candidates = ["epub", "cbz", "pdf"]
    elif detection.format == "epub":
        candidates = ["epub_optimized", *_FROM_EPUB]
    elif detection.format == "pdf":
        # A PDF stays a PDF only through the image pipeline; text ones reflow.
        candidates = ["epub"] + ([] if detection.image_only else ["pdf"])
    elif detection.format in _TEXT_INPUTS or detection.is_ebook or detection.is_document:
        candidates = ["epub"]

    results: list[dict] = []
    for fmt in candidates:
        converter, note, available = _resolve(fmt, detection, profile, chain)
        if not available:
            continue
        results.append(
            {
                "format": fmt,
                "label": OUTPUT_LABELS.get(fmt, fmt.upper()),
                "converter": converter,
                "note": note,
                "recommended": False,
            }
        )

    results = _order(results, profile)
    if results:
        results[0]["recommended"] = True
    return results


def _resolve(fmt: str, detection: Detection, profile, chain) -> tuple[str, str, bool]:
    if fmt == "epub" and detection.format in _WEB_FORMATS:
        return "webpage_to_epub", "Limpa a página e monta capítulos por seção.", True
    # A real book (text EPUB) becomes a paginated PDF through the layout engine;
    # page-based inputs go through the image pipeline instead.
    if fmt == "pdf" and detection.format == "epub" and not detection.image_only:
        if chain.has_pdf():
            return "epub_to_pdf", "Paginação nativa (PyMuPDF).", True
        return "epub_to_pdf", "Requer PyMuPDF.", False
    if fmt == "epub" and detection.format == "pdf":
        if not chain.has_pdf():
            return "pdf_to_epub", "Requer PyMuPDF.", False
        return "pdf_to_epub", "Reflow do texto do PDF (sem Calibre).", True
    if fmt == "epub" and detection.format in {"txt", "rtf"}:
        return "text_to_epub", "", True
    if fmt == "epub" and detection.format == "fb2":
        return "fb2_to_epub", "", True
    if fmt == "epub" and detection.format == "docx":
        if not chain.backends.get("docx"):
            return "docx_to_epub", "Requer python-docx.", False
        return "docx_to_epub", "", True
    if fmt == "epub" and detection.format in {"mobi", "azw", "azw3", "pdb", "prc"}:
        if not chain.backends.get("mobi"):
            return "kindle_to_epub", "Requer o pacote 'mobi'.", False
        return "kindle_to_epub", "", True
    if fmt in _PAGE_FORMATS:
        if fmt == "pdf" and detection.is_comic and profile and profile.comic_output == "epub_images":
            return "images_to_pdf", "Preserva o layout, mas não é nativo no Xteink.", True
        return f"images_to_{fmt}", "", True
    if fmt == "azw3" or fmt == "mobi":
        return "epub_to_kindle", "Escritor Kindle nativo (sem Calibre).", True
    if fmt == "kepub":
        return "epub_to_kepub", "Adiciona os spans de progresso do Kobo.", True
    if fmt == "pdf":
        if chain.has_pdf():
            return "epub_to_pdf", "Paginação nativa (PyMuPDF).", True
        return "images_to_pdf", "", True
    if fmt == "docx":
        if not chain.backends.get("docx"):
            return "epub_to_docx", "Requer python-docx.", False
        return "epub_to_docx", "", True
    if fmt == "fb2":
        return "epub_to_fb2", "", True
    if fmt == "txt":
        return "epub_to_text", "", True
    if fmt in {"jpg", "png"}:
        return "compress_image", "Redimensiona/compacta a imagem.", True
    if fmt == "epub_optimized":
        return "epub_optimize", "Mantém o texto e reduz as imagens.", True
    return fmt, "", True


def _order(results: list[dict], profile: DeviceProfile | None) -> list[dict]:
    if profile is None:
        return results
    preferred = profile.preferred_format

    def rank(item: dict) -> tuple[int, int]:
        fmt = item["format"]
        normalized = "epub" if fmt == "epub_optimized" else fmt
        # Native output for this device first, then the profile preference.
        native_rank = 0 if profile.can_open(normalized) else 1
        pref_rank = 0 if normalized == preferred else 1
        comic_rank = 0
        if profile.comic_output == "epub_images" and normalized == "epub":
            comic_rank = -1
        return (comic_rank + native_rank, pref_rank)

    return sorted(results, key=rank)
