"""Images/comics -> fixed-layout PDF (keeps scanned layouts intact)."""

from __future__ import annotations

from PIL import Image

from app.converters.base import ConversionRequest, ConversionResult
from app.converters.strategies.page_base import BasePageConverter


class ImagesToPdfConverter(BasePageConverter):
    name = "images_to_pdf"
    priority = 30
    target = "pdf"

    def run(self, request: ConversionRequest) -> ConversionResult:
        pages = self.pages(request)
        request.report(85, "Montando PDF")

        opened = []
        for page in pages:
            img = Image.open(page)
            if img.mode not in {"RGB", "L"}:
                img = img.convert("RGB")
            opened.append(img)

        out = request.workdir / "output.pdf"
        opened[0].save(
            out, "PDF", resolution=150.0, save_all=True, append_images=opened[1:]
        )
        request.report(100, "PDF pronto")
        return ConversionResult(
            output_path=out,
            converter=self.name,
            message=f"PDF com {len(pages)} páginas",
            page_count=len(pages),
        )
