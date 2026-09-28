"""EPUB -> PDF through PyMuPDF's ``Story`` engine -- no Calibre.

The EPUB is unpacked, its CSS and images are collected, the spine documents are
flattened into one HTML string (in spine order) and paginated by ``Story`` for
the target page box. Images are inlined as data URIs so the renderer can resolve
them without an archive/base path.
"""

from __future__ import annotations

import base64
import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

__all__ = ["epub_to_pdf"]

_XHTML_EXTS = (".xhtml", ".html", ".htm")
_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "svg"}
_MIME_BY_EXT = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
}
_BODY = re.compile(rb"<body[^>]*>(.*?)</body>", re.S | re.I)
_STYLE = re.compile(r"(?is)<style[^>]*>(.*?)</style>")
_LINK = re.compile(r"(?is)<link[^>]*?/?>")
_ATTR = re.compile(r"(?i)\b(src|xlink:href|href)\s*=\s*([\"'])(.*?)\2")
#: Raster images are re-encoded to JPEG; 150 DPI keeps text crisp without the
#: multi-hundred-MB raw bitmaps the renderer would otherwise embed.
_IMAGE_DPI = 150
_IMAGE_QUALITY = 82


def epub_to_pdf(
    source_epub: Path,
    out_path: Path,
    *,
    page_width_pt: float,
    page_height_pt: float,
    margin_pt: float = 18.0,
    title: str = "",
) -> None:
    """Paginate ``source_epub`` into a PDF at ``out_path`` for the given page box."""
    pymupdf = _import_pymupdf()

    with zipfile.ZipFile(source_epub) as zf:
        opf_path = _find_opf(zf)
        opf_dir = posixpath.dirname(opf_path) if opf_path else ""
        manifest, spine = _parse_opf(zf, opf_path) if opf_path else ({}, [])
        if not manifest:
            manifest, spine = _fallback_manifest(zf, opf_dir)

        images = _collect_images(
            zf, manifest, _target_image_side(page_width_pt, page_height_pt), opf_dir
        )
        css_parts: list[str] = []
        bodies: list[str] = []

        spine_items = [
            manifest[idref] for idref in spine if idref in manifest
        ] or [item for item in manifest.values() if _is_markup(item["href"])]

        for item in spine_items:
            if item.get("media_type", "").startswith("image/"):
                continue
            markup = _read_item(zf, opf_dir, item["href"])
            if markup is None:
                continue
            for style in _STYLE.findall(markup):
                css_parts.append(style)
            markup = _STYLE.sub("", markup)
            markup = _LINK.sub("", markup)
            body = _body_inner(markup)
            # Relative src="/images/..." is relative to the *document*, not to the
            # OPF: resolving against the OPF dir turned the cover into a broken
            # placeholder in the PDF.
            document_dir = posixpath.dirname(_resolve(opf_dir, item["href"]))
            body = _inline_images(body, document_dir, images)
            if body.strip():
                bodies.append(body)

        for item in manifest.values():
            if item.get("media_type") == "text/css" or item["href"].lower().endswith(".css"):
                text = _read_item(zf, opf_dir, item["href"])
                if text:
                    css_parts.append(text)

    html_doc = (
        '<html><head><meta charset="utf-8"/></head><body>'
        + "\n".join(bodies)
        + "</body></html>"
    )
    user_css = "\n".join(css_parts) or None

    width = float(page_width_pt) if page_width_pt and page_width_pt > 0 else 595.0
    height = float(page_height_pt) if page_height_pt and page_height_pt > 0 else 842.0
    margin = max(0.0, float(margin_pt))
    if 2 * margin >= width or 2 * margin >= height:
        margin = 0.0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    story = pymupdf.Story(html=html_doc, user_css=user_css)
    mediabox = pymupdf.Rect(0, 0, width, height)
    where = pymupdf.Rect(margin, margin, width - margin, height - margin)

    writer = pymupdf.DocumentWriter(str(out_path))
    more = 1
    pages = 0
    try:
        while more:
            device = writer.begin_page(mediabox)
            more, _filled = story.place(where)
            story.draw(device)
            writer.end_page()
            pages += 1
            if pages >= 10000:  # safety valve against a malformed stylesheet
                break
    finally:
        writer.close()

    if title:
        _set_title(pymupdf, out_path, title)


# ---------------------------------------------------------------------------
# EPUB package parsing
# ---------------------------------------------------------------------------
def _find_opf(zf: zipfile.ZipFile) -> str | None:
    try:
        root = ElementTree.fromstring(zf.read("META-INF/container.xml"))
    except (KeyError, ElementTree.ParseError):
        return None
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "rootfile" and element.get("full-path"):
            return element.get("full-path")
    return None


def _parse_opf(zf: zipfile.ZipFile, opf_path: str) -> tuple[dict, list[str]]:
    try:
        root = ElementTree.fromstring(zf.read(opf_path))
    except (KeyError, ElementTree.ParseError):
        return {}, []

    manifest: dict[str, dict] = {}
    spine: list[str] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "item" and element.get("id") and element.get("href"):
            manifest[element.get("id")] = {
                "href": element.get("href"),
                "media_type": element.get("media-type") or "",
            }
        elif tag == "itemref" and element.get("idref"):
            spine.append(element.get("idref"))
    return manifest, spine


def _fallback_manifest(zf: zipfile.ZipFile, opf_dir: str) -> tuple[dict, list[str]]:
    """Best-effort index for EPUBs without a readable OPF."""
    manifest: dict[str, dict] = {}
    order: list[str] = []
    for index, name in enumerate(zf.namelist()):
        if name.endswith("/"):
            continue
        item_id = f"item{index}"
        manifest[item_id] = {"href": name, "media_type": _guess_media(name)}
        if _is_markup(name):
            order.append(item_id)
    return manifest, order


def _collect_images(
    zf: zipfile.ZipFile, manifest: dict, max_long_side: int, base: str
) -> dict[str, str]:
    images: dict[str, str] = {}
    for item in manifest.values():
        href = item["href"]
        ext = href.rsplit(".", 1)[-1].lower()
        if not item.get("media_type", "").startswith("image/") and ext not in _IMAGE_EXTS:
            continue
        # The OPF hrefs are relative to the OPF, which is not always at the root.
        raw = None
        for candidate in (_resolve(base, href), href):
            try:
                raw = zf.read(candidate)
                break
            except KeyError:
                continue
        if raw is None:
            continue
        mime = item.get("media_type") or _MIME_BY_EXT.get(ext, "image/png")
        data, mime = _compress_image(raw, mime, max_long_side)
        uri = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        images[href] = uri
        images[_resolve(base, href)] = uri
        images[posixpath.basename(href)] = uri
    return images


def _target_image_side(page_width_pt: float, page_height_pt: float) -> int:
    long_pt = max(float(page_width_pt or 0), float(page_height_pt or 0), 1.0)
    pixels = round(long_pt * _IMAGE_DPI / 72.0)
    return max(600, min(2400, pixels))


def _compress_image(data: bytes, mime: str, max_long_side: int) -> tuple[bytes, str]:
    """Downscale and re-encode raster images so the PDF stays small.

    The renderer embeds images without recompression, so a raw 2500px PNG would
    produce a ~30MB page. JPEG at a bounded size keeps the file sane.
    """
    if mime == "image/svg+xml" or data[:5] == b"<?xml":
        return data, mime
    try:
        from app.converters.imageops import cap_long_side, encode_image, open_image

        img = cap_long_side(open_image(data), max_long_side)
        return encode_image(img, "jpg", quality=_IMAGE_QUALITY), "image/jpeg"
    except Exception:
        return data, mime  # Pillow cannot read it: leave the original bytes


def _read_text(zf: zipfile.ZipFile, href: str) -> str | None:
    try:
        return zf.read(href).decode("utf-8", "replace")
    except KeyError:
        return None


def _read_item(zf: zipfile.ZipFile, opf_dir: str, href: str) -> str | None:
    """Read a manifest item, honouring the OPF directory.

    The hrefs inside the OPF are relative to the OPF itself, which is not always
    at the root of the zip (ours lives in ``OEBPS/``): without this the spine
    documents were silently missing and the PDF came out empty.
    """
    text = _read_text(zf, _resolve(opf_dir, href))
    if text is None:
        text = _read_text(zf, href)
    return text


def _inline_images(markup: str, base: str, images: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        attr, quote, url = match.group(1), match.group(2), match.group(3)
        if url.startswith(("data:", "http:", "https:", "#")):
            return match.group(0)
        resolved = _resolve(base, url)
        uri = images.get(resolved) or images.get(posixpath.basename(resolved))
        if not uri:
            return match.group(0)
        return f"{attr}={quote}{uri}{quote}"

    return _ATTR.sub(replace, markup)


def _resolve(base: str, href: str) -> str:
    href = href.split("#", 1)[0].split("?", 1)[0]
    return posixpath.normpath(posixpath.join(base, href)) if base else posixpath.normpath(href)


def _body_inner(markup: str) -> str:
    match = _BODY.search(markup.encode("utf-8", "replace"))
    if match:
        return match.group(1).decode("utf-8", "replace")
    return markup


def _is_markup(href: str) -> bool:
    return href.lower().endswith(_XHTML_EXTS)


def _guess_media(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower()
    if ext in {"xhtml", "html", "htm"}:
        return "application/xhtml+xml"
    if ext == "css":
        return "text/css"
    if ext in _IMAGE_EXTS:
        return _MIME_BY_EXT.get(ext, "image/png")
    return "application/octet-stream"


def _set_title(pymupdf, path: Path, title: str) -> None:
    try:
        with pymupdf.open(str(path)) as doc:
            meta = doc.metadata or {}
            meta["title"] = title
            doc.set_metadata(meta)
            doc.save(str(path), incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        return
    except Exception:
        pass
    try:
        tmp = path.with_name(path.stem + ".meta.pdf")
        with pymupdf.open(str(path)) as doc:
            meta = doc.metadata or {}
            meta["title"] = title
            doc.set_metadata(meta)
            doc.save(str(tmp))
        tmp.replace(path)
    except Exception:
        pass  # metadata is a nicety, never a reason to fail the conversion


def _import_pymupdf():
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise RuntimeError(
                "PyMuPDF é necessário para gerar PDF (pip install PyMuPDF)."
            ) from exc
    return pymupdf
