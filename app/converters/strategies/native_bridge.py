"""Native backends wrapped in the converter interface.

``app/converters/native`` holds the engines (PDF text reflow, PDF writing and
the Kindle KF8/MOBI writer). This module adapts them to :class:`BaseConverter`
so the registry can pick them exactly like any other strategy -- and so nothing
in the application needs Calibre installed.
"""

from __future__ import annotations

from app.converters.base import BaseConverter, ConversionError, ConversionRequest, ConversionResult
from app.converters.epub.model import EpubMeta
from app.converters.epub.text_builder import read_cover, write_text_epub

#: Page geometry for EPUB -> PDF: keep the device aspect ratio at a readable size.
PDF_BASE_WIDTH_PT = 420.0
PDF_MIN_HEIGHT_PT = 500.0
PDF_MAX_HEIGHT_PT = 900.0


class PdfToEpubTextConverter(BaseConverter):
    """PDF with a text layer -> EPUB, reflowed natively (PyMuPDF)."""

    name = "pdf_to_epub"
    priority = 58

    def can_handle(self, request: ConversionRequest) -> bool:
        if request.target_format != "epub" or request.detection.format != "pdf":
            return False
        # Scanned PDFs have no text: the image pipeline handles those.
        return not request.detection.image_only

    def supports_pair(self, source_fmt: str, target_fmt: str) -> bool:
        return source_fmt == "pdf" and target_fmt == "epub"

    def run(self, request: ConversionRequest) -> ConversionResult:
        from app.converters.native.pdf_text import extract_pdf_text

        request.report(10, "Extraindo o texto do PDF")
        try:
            extracted = extract_pdf_text(request.source)
        except Exception as exc:  # noqa: BLE001 - one clear message for the user
            raise ConversionError(f"Não consegui ler o texto do PDF: {exc}") from exc

        if not extracted.chapters:
            raise ConversionError("Este PDF não tem texto selecionável.")

        metadata = request.metadata
        meta = EpubMeta(
            title=metadata.title or extracted.title or request.source.stem,
            author=metadata.author or extracted.author or "",
            language=metadata.language or extracted.language or "pt",
            publisher=metadata.publisher or "",
            description=metadata.description or "",
            date=metadata.published or None,
            subjects=list(metadata.tags or []),
            identifier=request.source.name,
            generator="inkport (pdf-reflow)",
        )
        out = request.workdir / "output.epub"
        write_text_epub(
            out,
            meta=meta,
            chapters=extracted.chapters,
            images=extracted.images,
            cover=read_cover(request.options.get("cover_path")),
        )
        request.report(100, "EPUB pronto")
        return ConversionResult(
            output_path=out,
            converter=self.name,
            message=f"{len(extracted.chapters)} capítulo(s) de {extracted.page_count} página(s)",
            page_count=extracted.page_count,
        )


class EpubToPdfConverter(BaseConverter):
    """EPUB -> PDF, paginated with PyMuPDF's layout engine."""

    name = "epub_to_pdf"
    priority = 58

    def can_handle(self, request: ConversionRequest) -> bool:
        return request.target_format == "pdf" and request.detection.format == "epub"

    def supports_pair(self, source_fmt: str, target_fmt: str) -> bool:
        return source_fmt == "epub" and target_fmt == "pdf"

    def run(self, request: ConversionRequest) -> ConversionResult:
        from app.converters.native.pdf_writer import epub_to_pdf

        width, height = pdf_page_size(request)
        out = request.workdir / "output.pdf"
        request.report(20, "Montando o PDF")
        try:
            epub_to_pdf(
                request.source,
                out,
                page_width_pt=width,
                page_height_pt=height,
                margin_pt=20.0,
                title=request.metadata.title or "",
            )
        except Exception as exc:  # noqa: BLE001 - one clear message for the user
            raise ConversionError(f"Não consegui montar o PDF: {exc}") from exc
        request.report(100, "PDF pronto")
        return ConversionResult(
            output_path=out, converter=self.name, message="Convertido para PDF"
        )


class EpubToKindleConverter(BaseConverter):
    """EPUB -> AZW3 (KF8) or MOBI, written natively (no Calibre, no kindlegen)."""

    name = "epub_to_kindle"
    priority = 61

    def can_handle(self, request: ConversionRequest) -> bool:
        if request.target_format not in {"azw3", "mobi"}:
            return False
        if request.detection.format != "epub":
            return False
        from app.converters.native.kindle import kindle_available

        return kindle_available()

    def supports_pair(self, source_fmt: str, target_fmt: str) -> bool:
        return source_fmt == "epub" and target_fmt in {"azw3", "mobi"}

    def run(self, request: ConversionRequest) -> ConversionResult:
        from app.converters.native.kindle import epub_to_kindle

        target = "mobi" if request.target_format == "mobi" else "azw3"
        out = request.workdir / f"output.{target}"
        metadata = request.metadata
        request.report(15, "Montando o arquivo Kindle")
        try:
            epub_to_kindle(
                request.source,
                out,
                target=target,
                title=metadata.title or "",
                author=metadata.author or "",
                language=metadata.language or "",
                publisher=metadata.publisher or "",
                description=metadata.description or "",
                series=metadata.series or "",
                series_index=metadata.series_index,
            )
        except Exception as exc:  # noqa: BLE001 - one clear message for the user
            raise ConversionError(f"Não consegui gerar o {target.upper()}: {exc}") from exc
        request.report(100, f"{target.upper()} pronto")
        return ConversionResult(
            output_path=out,
            converter=self.name,
            message=f"Convertido para {target.upper()} (nativo)",
        )


def pdf_page_size(request: ConversionRequest) -> tuple[float, float]:
    """Page size for the PDF target: the device aspect ratio, at a readable size."""
    profile = request.profile
    width = float(getattr(profile, "screen_width", 0) or 0)
    height = float(getattr(profile, "screen_height", 0) or 0)
    if width <= 0 or height <= 0:
        return PDF_BASE_WIDTH_PT, PDF_BASE_WIDTH_PT * 1.4
    ratio = height / width
    if getattr(profile, "rotate_portrait", False):
        ratio = width / height
    page_height = min(PDF_MAX_HEIGHT_PT, max(PDF_MIN_HEIGHT_PT, PDF_BASE_WIDTH_PT * ratio))
    return PDF_BASE_WIDTH_PT, page_height
