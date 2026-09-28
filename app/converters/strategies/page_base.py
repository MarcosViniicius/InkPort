"""Shared base for converters that build output out of page images."""

from __future__ import annotations

from pathlib import Path

from app.converters.base import (
    BaseConverter,
    ConversionError,
    ConversionRequest,
)
from app.converters.collectors import collect_pages
from app.converters.normalise import process_images

PAGE_SOURCES = {"image", "comic", "document"}


class BasePageConverter(BaseConverter):
    target = "epub"
    source_contents = PAGE_SOURCES

    def can_handle(self, request: ConversionRequest) -> bool:
        if request.target_format != self.target:
            return False
        if request.detection.content_type in self.source_contents:
            return True
        # Extract the images out of an EPUB into CBZ/PDF (never back to EPUB;
        # EPUB -> EPUB is handled by the optimiser).
        return request.detection.format == "epub" and self.target in {"cbz", "pdf"}

    def pages(self, request: ConversionRequest) -> list[Path]:
        """Collect source pages and normalise them for the target device."""
        profile = request.profile
        options = request.options
        try:
            raw = collect_pages(
                request.source,
                request.detection,
                request.workdir,
                target=(profile.target_width, profile.target_height),
                dpi=int(options.get("dpi", profile.render_dpi) or 0),
                grayscale=bool(options.get("grayscale", profile.grayscale))
                and not bool(options.get("colour", profile.color)),
                max_long_side=int(options.get("max_long_side", profile.max_long_side) or 0),
            )
        except Exception as exc:
            raise ConversionError(f"Falha ao extrair páginas: {exc}") from exc
        if not raw:
            raise ConversionError("Nenhuma página encontrada no arquivo de origem.")
        request.report(10, f"{len(raw)} páginas encontradas")
        return process_images(
            raw,
            request.profile,
            request.workdir / "out_pages",
            options=request.options,
            progress=request.report,
        )
