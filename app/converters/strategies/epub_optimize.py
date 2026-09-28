"""EPUB -> EPUB optimisation: recompress embedded images, keep text untouched.

Uses the same normalisation pipeline as the other converters, so a profile's
quality, grayscale, quantisation and resize rules apply here too. Handy for
shrinking a file that already exists without re-converting from the source.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.converters.base import (
    BaseConverter,
    ConversionError,
    ConversionRequest,
    ConversionResult,
)
from app.converters.imageops import encode_image, open_image
from app.converters.normalise import resolve_options, transform_page

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
SAVEABLE = {"jpg", "jpeg", "png", "webp", "bmp"}


class EpubOptimizerConverter(BaseConverter):
    name = "epub_optimize"
    priority = 70

    def can_handle(self, request: ConversionRequest) -> bool:
        return request.target_format == "epub" and request.detection.format == "epub"

    def run(self, request: ConversionRequest) -> ConversionResult:
        out = request.workdir / "output.epub"
        try:
            self._rebuild(request, out)
        except zipfile.BadZipFile as exc:
            raise ConversionError("EPUB inválido ou corrompido.") from exc

        request.report(100, "EPUB otimizado")
        cover = request.options.get("cover_path")
        return ConversionResult(
            output_path=out,
            converter=self.name,
            message=f"Imagens recompressadas ({request.profile.slug})",
            page_count=request.metadata.page_count,
            cover_path=Path(cover) if cover else None,
        )

    def _rebuild(self, request: ConversionRequest, out: Path) -> None:
        profile = request.profile
        opts = resolve_options(profile, request.options)

        with zipfile.ZipFile(request.source) as src, zipfile.ZipFile(
            out, "w", zipfile.ZIP_DEFLATED
        ) as dst:
            images = [n for n in src.namelist() if _is_image(n)]
            done = 0
            for info in src.infolist():
                name = info.filename
                if name == "mimetype":
                    dst.writestr(
                        zipfile.ZipInfo("mimetype"),
                        src.read(name),
                        compress_type=zipfile.ZIP_STORED,
                    )
                    continue

                data = src.read(name)
                if _is_image(name):
                    data = self._process(data, name, profile, opts)
                    done += 1
                    if images:
                        request.report(int(done / len(images) * 90), f"{done}/{len(images)} imagens")
                    dst.writestr(name, data, compress_type=zipfile.ZIP_STORED)
                else:
                    dst.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)

    def _process(self, data: bytes, name: str, profile, opts: dict) -> bytes:
        ext = name.rsplit(".", 1)[-1].lower()
        if ext not in SAVEABLE:
            return data
        try:
            img = open_image(io.BytesIO(data))
        except Exception:
            return data

        # PNG stays PNG (lossless container already in the book); otherwise the
        # profile decides. This avoids rewriting the OPF/hrefs.
        target_ext = "png" if ext == "png" else opts["image_format"]

        already_fine = (
            img.width <= profile.target_width
            and img.height <= profile.target_height
            and not opts["grayscale"]
            and not opts["posterize_levels"]
            and not opts["crop_margins"]
        )
        if already_fine:
            return data

        img, grayscale = transform_page(img, profile, opts)
        return encode_image(img, target_ext, quality=opts["quality"], grayscale=grayscale)


def _is_image(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_SUFFIXES
