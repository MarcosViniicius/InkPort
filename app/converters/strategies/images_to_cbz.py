"""Images/comics/PDF -> CBZ archive."""

from __future__ import annotations

import zipfile

from app.converters.base import ConversionRequest, ConversionResult
from app.converters.strategies.comicinfo import build_comicinfo
from app.converters.strategies.page_base import BasePageConverter


class ImagesToCbzConverter(BasePageConverter):
    name = "images_to_cbz"
    priority = 40
    target = "cbz"

    def run(self, request: ConversionRequest) -> ConversionResult:
        pages = self.pages(request)
        request.report(85, "Empacotando CBZ")

        out = request.workdir / "output.cbz"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for index, page in enumerate(pages, start=1):
                ext = page.suffix.lower().lstrip(".") or "jpg"
                zf.write(page, f"{index:05d}.{ext}")
            zf.writestr("ComicInfo.xml", build_comicinfo(request))

        request.report(100, "CBZ pronto")
        return ConversionResult(
            output_path=out,
            converter=self.name,
            message=f"CBZ com {len(pages)} páginas",
            page_count=len(pages),
            cover_path=pages[0],
        )
