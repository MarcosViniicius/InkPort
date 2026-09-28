"""Assemble an EPUB file from a list of (already device-sized) page images."""

from __future__ import annotations

import zipfile
from pathlib import Path

from app.converters.epub.model import (
    CONTAINER_XML,
    MIMETYPE,
    STYLES_CSS,
    EpubMeta,
)
from app.converters.epub.opf import build_opf
from app.converters.epub.templates import cover_xhtml, nav_xhtml, ncx_xml, page_xhtml, title_xhtml


def write_epub(
    out_path: Path,
    *,
    images: list[Path],
    meta: EpubMeta | None = None,
    include_title_page: bool = False,
) -> Path:
    meta = meta or EpubMeta()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cover_href: str | None = None
    cover_bytes: bytes | None = None
    if meta.cover_image and meta.cover_image.exists():
        cover_href = "images/cover.jpg"
        cover_bytes = meta.cover_image.read_bytes()

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        _write_mimetype(zf)
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/styles.css", STYLES_CSS)

        image_hrefs, page_hrefs = _write_pages(zf, images)

        if cover_href and cover_bytes is not None:
            zf.writestr(f"OEBPS/{cover_href}", cover_bytes)
            zf.writestr("OEBPS/cover.xhtml", cover_xhtml(f"../{cover_href}", meta))
        if include_title_page:
            zf.writestr("OEBPS/title.xhtml", title_xhtml(meta))

        entries: list[tuple[str, str]] = []
        if cover_href:
            entries.append(("Capa", "cover.xhtml"))
        if include_title_page:
            entries.append(("Título", "title.xhtml"))
        entries.extend((f"Página {i}", href) for i, href in enumerate(page_hrefs, 1))

        zf.writestr("OEBPS/nav.xhtml", nav_xhtml(entries))
        zf.writestr("OEBPS/toc.ncx", ncx_xml(meta, entries))
        zf.writestr(
            "OEBPS/content.opf",
            build_opf(meta, image_hrefs, page_hrefs, cover_href, include_title_page),
        )

    return out_path


def _write_mimetype(zf: zipfile.ZipFile) -> None:
    # The mimetype entry MUST be first and stored uncompressed (EPUB spec).
    zf.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED)


def _write_pages(
    zf: zipfile.ZipFile, images: list[Path]
) -> tuple[list[str], list[str]]:
    image_hrefs: list[str] = []
    page_hrefs: list[str] = []
    total = len(images)
    for index, image in enumerate(images, start=1):
        ext = image.suffix.lower().lstrip(".") or "jpg"
        image_href = f"images/page_{index:05d}.{ext}"
        # PNG/JPEG are already compressed: storing avoids a pointless deflate pass.
        zf.write(image, f"OEBPS/{image_href}", compress_type=zipfile.ZIP_STORED)
        image_hrefs.append(image_href)

        page_href = f"text/page_{index:05d}.xhtml"
        zf.writestr(
            f"OEBPS/{page_href}",
            page_xhtml(f"../{image_href}", index=index, total=total),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        page_hrefs.append(page_href)
    return image_hrefs, page_hrefs
