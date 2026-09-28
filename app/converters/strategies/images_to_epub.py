"""Images/comics/PDF -> image-based EPUB (the primary output for e-ink)."""

from __future__ import annotations

from pathlib import Path

from app.converters.base import ConversionRequest, ConversionResult
from app.converters.epub import EpubMeta, write_epub
from app.converters.strategies.comicinfo import reading_direction
from app.converters.strategies.page_base import BasePageConverter


class ImagesToEpubConverter(BasePageConverter):
    name = "images_to_epub"
    priority = 50
    target = "epub"

    def run(self, request: ConversionRequest) -> ConversionResult:
        pages = self.pages(request)
        request.report(85, "Montando EPUB")

        meta = request.metadata
        cover = request.options.get("cover_path")
        cover_path = Path(cover) if cover else pages[0]

        out = request.workdir / "output.epub"
        write_epub(
            out,
            images=pages,
            meta=EpubMeta(
                title=meta.title or request.source.stem,
                author=meta.author,
                language=meta.language or "pt",
                publisher=meta.publisher,
                description=meta.description,
                series=meta.series,
                series_index=meta.series_index,
                subjects=list(meta.tags or []),
                reading_direction=reading_direction(request),
                cover_image=cover_path,
                fixed_layout=True,
            ),
            include_title_page=bool(request.options.get("title_page", False)),
        )
        request.report(100, "EPUB pronto")
        return ConversionResult(
            output_path=out,
            converter=self.name,
            message=f"EPUB com {len(pages)} páginas",
            page_count=len(pages),
            cover_path=cover_path,
        )
