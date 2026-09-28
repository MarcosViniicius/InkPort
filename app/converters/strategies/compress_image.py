"""Single image -> device-sized image (resize / compress / grayscale)."""

from __future__ import annotations

from app.converters.base import BaseConverter, ConversionRequest, ConversionResult
from app.converters.normalise import process_images


class CompressImageConverter(BaseConverter):
    name = "compress_image"
    priority = 60

    def can_handle(self, request: ConversionRequest) -> bool:
        if request.detection.content_type != "image":
            return False
        return request.target_format in {"jpg", "jpeg", "png", "webp", "bmp"}

    def run(self, request: ConversionRequest) -> ConversionResult:
        pages = process_images(
            [request.source],
            request.profile,
            request.workdir / "out_pages",
            options=request.options,
            progress=request.report,
        )
        target = request.target_format.lower().replace("jpeg", "jpg")
        out = request.workdir / f"output.{target}"
        out.write_bytes(pages[0].read_bytes())

        return ConversionResult(
            output_path=out,
            converter=self.name,
            message="Imagem otimizada",
            cover_path=out,
        )
