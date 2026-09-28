"""Webpage -> EPUB.

A Python port of the reference implementation used by
https://webpagetoepub.github.io/ (html2epub, MIT): clean the document, replace
unsupported elements, pick the main content, download the images, split the text
into chapters by heading and fix the links.

The pipeline mirrors the original step order so the result matches it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.converters.webpage.clean import clean_document
from app.converters.webpage.dom import inner_xhtml, parse
from app.converters.webpage.images import ImageResource, load_images, prepare_lazy_images
from app.converters.webpage.links import fix_links
from app.converters.webpage.main_content import get_main_content
from app.converters.webpage.metadata import PageMetadata, get_metadata
from app.converters.webpage.replace import replace_elements
from app.converters.webpage.split import consolidate_chapters, split_main_content

__all__ = [
    "ImageResource",
    "PageMetadata",
    "Webpage",
    "convert_webpage",
    "get_metadata",
]


@dataclass(slots=True)
class Webpage:
    title: str
    author: str = ""
    language: str = "en"
    description: str = ""
    publisher: str = ""
    date: str | None = None
    tags: list[str] = field(default_factory=list)
    identifier: str = ""
    chapters: list[tuple[str, str]] = field(default_factory=list)
    images: list[ImageResource] = field(default_factory=list)


def convert_webpage(
    html_data: bytes | str,
    page_url: str,
    fetch_image,
) -> Webpage:
    """Run the full pipeline. ``fetch_image(url) -> (bytes, mime) | None``.

    Step order follows the reference (clean -> main content -> replace -> images
    -> split -> links). One deliberate difference: the reference replaces
    ``main``/``article`` by ``div`` *before* choosing the content, so its
    selector always falls back to the whole ``<body>`` and site headers/footers
    leak into the book. We choose the content first, which is what
    ``get_main_content`` clearly intends.
    """
    document = parse(html_data)
    metadata = get_metadata(document, page_url)

    prepare_lazy_images(document)
    clean_document(document)

    # Choose the content, then remember it across the tag replacements below
    # (replacing <main>/<article> by <div> detaches the element we selected).
    main_element = get_main_content(document)
    marker = "data-webpage-root"
    main_element.set(marker, "1")

    replace_elements(document)

    marked = document.xpath(f"//*[@{marker}]")
    if marked:
        main_element = marked[0]
        main_element.attrib.pop(marker, None)

    images = load_images(main_element, page_url, fetch_image)

    parts = split_main_content(main_element, metadata.title or "Artigo")
    fix_links(parts, page_url)

    chapters = [
        ((title or metadata.title or "Artigo").strip(), inner_xhtml(element))
        for title, element in parts
    ]
    chapters = consolidate_chapters(chapters)
    if not chapters:
        chapters = [(metadata.title or "Artigo", "<p></p>")]

    return Webpage(
        title=metadata.title or "Artigo",
        author=metadata.author,
        language=metadata.language,
        description=metadata.description,
        publisher=metadata.publisher,
        date=metadata.date,
        tags=metadata.tags,
        identifier=metadata.identifier or page_url,
        chapters=chapters,
        images=images,
    )
