"""Image handling (port of ``load_images.ts``).

Every ``<img>`` is resolved to an absolute URL, downloaded once (memoised) and
replaced by a reference to the resource embedded in the EPUB. Images that fail
to download are removed, matching the reference behaviour of keeping only the
images that actually loaded.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from urllib.parse import urljoin

from lxml import etree

from app.converters.webpage.dom import remove

logger = logging.getLogger(__name__)

_EXT_BY_MIME = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/svg+xml": "svg",
    "image/avif": "avif",
}


@dataclass(slots=True)
class ImageResource:
    name: str
    data: bytes
    mime: str


def prepare_lazy_images(document: etree._Element) -> int:
    """Promote lazy-loading image sources to ``src``.

    The reference removes ``data-*`` and ``srcset`` early, so images that modern
    sites load lazily would be lost. Run this before cleaning: it is a small
    addition that keeps the rest of the pipeline identical.
    """
    promoted = 0
    lazy_attributes = (
        "data-src", "data-original", "data-lazy-src", "data-url", "data-image",
        "data-full-src", "data-hi-res-src",
    )
    for image in document.iter("img"):
        if (image.get("src") or "").strip():
            continue
        for attribute in lazy_attributes:
            value = (image.get(attribute) or "").strip()
            if value:
                image.set("src", value)
                promoted += 1
                break
        else:
            source = _picture_source(image)
            if source:
                image.set("src", source)
                promoted += 1
    return promoted


def _picture_source(image: etree._Element) -> str | None:
    parent = image.getparent()
    if parent is None or parent.tag != "picture":
        return None
    for source in parent.iter("source"):
        srcset = (source.get("srcset") or "").strip()
        if srcset:
            candidate = srcset.split(",")[0].strip().split(" ")[0]
            if candidate:
                return candidate
    return None


def load_images(
    main_element: etree._Element,
    page_url: str,
    fetch,
    *,
    max_images: int = 200,
) -> list[ImageResource]:
    cache: dict[str, ImageResource | None] = {}
    resources: list[ImageResource] = []

    images = [
        element
        for element in main_element.xpath(".//img[@src]")
        if (element.get("src") or "").strip()
        and not (element.get("src") or "").startswith("data:")
    ]

    for image in images[:max_images]:
        src = (image.get("src") or "").strip()
        absolute = urljoin(page_url, src)

        if absolute not in cache:
            cache[absolute] = _download(absolute, fetch)
        resource = cache[absolute]

        if resource is None:
            remove(image)
            continue

        if resource not in resources:
            resources.append(resource)

        parent = image.getparent()
        if parent is None:
            continue
        replacement = etree.Element("img")
        replacement.set("src", f"images/{resource.name}")
        alt = image.get("alt")
        if alt:
            replacement.set("alt", alt)
        tail = image.tail
        parent.replace(image, replacement)
        replacement.tail = tail

    return resources


def _download(url: str, fetch) -> ImageResource | None:
    try:
        result = fetch(url)
    except Exception as exc:  # a missing image must not fail the conversion
        logger.debug("image download failed", extra={"url": url, "error": str(exc)})
        return None

    if result is None:
        return None

    data, mime = result
    if not data or not (mime or "").startswith("image/"):
        return None

    ext = _EXT_BY_MIME.get((mime or "").split(";")[0].strip().lower(), "jpg")
    name = f"{_hash(url)}.{ext}"
    return ImageResource(name=name, data=data, mime=mime.split(";")[0].strip())


def _hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
