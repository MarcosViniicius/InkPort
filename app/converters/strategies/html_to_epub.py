"""Webpage (HTML) -> EPUB.

Implements the same pipeline as https://webpagetoepub.github.io/ (the
``html2epub`` library): clean the document, replace unsupported elements, pick
the main content, download the images, split into chapters by heading and fix
the links. Calibre (priority 55) is used only when this converter does not
apply, so the requested behaviour wins for web pages.
"""

from __future__ import annotations

import io

from app.converters.base import (
    BaseConverter,
    ConversionError,
    ConversionRequest,
    ConversionResult,
)
from app.converters.epub import EpubMeta
from app.converters.epub.text_builder import read_cover, write_text_epub
from app.converters.imageops import encode_image, open_image
from app.converters.normalise import resolve_options, transform_page
from app.converters.webpage import ImageFetcher, ImageResource, convert_webpage

WEB_FORMATS = {"html", "htm", "xhtml"}
SAVEABLE = {"jpg", "jpeg", "png", "webp", "bmp"}


class HtmlToEpubConverter(BaseConverter):
    name = "webpage_to_epub"
    #: Above Calibre (55): the reference webpage pipeline handles web pages.
    priority = 60

    def can_handle(self, request: ConversionRequest) -> bool:
        return (
            request.target_format == "epub"
            and request.detection.format in WEB_FORMATS
        )

    def run(self, request: ConversionRequest) -> ConversionResult:
        try:
            raw = request.source.read_bytes()
        except OSError as exc:
            raise ConversionError(f"Falha ao ler a página: {exc}") from exc

        page_url = request.options.get("source_url") or ""
        if not page_url:
            page_url = request.source.as_uri()

        request.report(20, "Limpando a página")
        with ImageFetcher() as fetcher:
            page = convert_webpage(raw, page_url, fetcher)

        if not page.chapters:
            raise ConversionError("Não encontrei conteúdo legível na página.")

        request.report(75, f"{len(page.chapters)} capítulo(s)")

        images = self._fit_images(page.images, request)

        meta = EpubMeta(
            title=request.metadata.title or page.title,
            author=request.metadata.author or page.author,
            language=request.metadata.language or page.language or "en",
            description=request.metadata.description or page.description,
            publisher=request.metadata.publisher or page.publisher,
            date=request.metadata.published or page.date,
            subjects=list(request.metadata.tags or []) or list(page.tags),
            identifier=page.identifier or page_url,
            generator="inkport (webpage-to-epub)",
        )

        out = request.workdir / "output.epub"
        write_text_epub(
            out,
            meta=meta,
            chapters=page.chapters,
            images=[(image.name, image.data, image.mime) for image in images],
            cover=read_cover(request.options.get("cover_path")),
        )

        request.report(100, "EPUB pronto")
        return ConversionResult(
            output_path=out,
            converter=self.name,
            message=(
                f"Página convertida: {len(page.chapters)} capítulo(s), "
                f"{len(images)} imagem(ns)"
            ),
            page_count=len(page.chapters),
        )

    def _fit_images(
        self, images: list[ImageResource], request: ConversionRequest
    ) -> list[ImageResource]:
        """Run article images through the target profile (size, gray, levels).

        The reference embeds the original files; on an e-reader that means
        multi-megabyte images in the wrong resolution. Reusing the image
        pipeline keeps the chapter references valid (same file name/extension)
        while making the book small and sharp on the device.
        """
        if not images:
            return images

        profile = request.profile
        opts = resolve_options(profile, request.options)
        result: list[ImageResource] = []

        for image in images:
            ext = image.name.rsplit(".", 1)[-1].lower()
            if ext not in SAVEABLE:
                result.append(image)  # svg/avif: keep as-is
                continue
            try:
                img = open_image(io.BytesIO(image.data))
            except Exception:
                continue

            already_fine = (
                img.width <= profile.target_width
                and img.height <= profile.target_height
                and not opts["grayscale"]
                and not opts["posterize_levels"]
                and not opts["preserve_resolution"]
            )
            if already_fine:
                result.append(image)
                continue

            img, grayscale = transform_page(img, profile, opts)
            target_ext = "jpg" if ext in {"jpg", "jpeg"} else ext
            data = encode_image(img, target_ext, quality=opts["quality"], grayscale=grayscale)
            mime = {
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "png": "image/png",
                "webp": "image/webp",
                "bmp": "image/bmp",
            }[ext]
            result.append(ImageResource(name=image.name, data=data, mime=mime))

        return result
