"""Replace-element steps (port of ``replace_elements/*.ts``)."""

from __future__ import annotations

from lxml import etree

from app.converters.webpage.dom import create, replace_keeping_children
from app.converters.webpage.tags import (
    ELEMENTS_WITH_CSS,
    HEADING_MAP,
    HTML5_TAGS,
    SIMPLE_TAGS_TO_REPLACE,
)


def replace_elements(doc: etree._Element) -> None:
    reduce_heading_level(doc)
    replace_simple_elements_tag(doc)
    replace_unknown_elements(doc)
    replace_elements_by_other_with_css(doc)


def reduce_heading_level(doc: etree._Element) -> None:
    """If the page has an <h1>, shift every heading one level down."""
    if not any(True for _ in doc.iter("h1")):
        return
    for heading in list(doc.iter("h1", "h2", "h3", "h4", "h5", "h6")):
        tag = heading.tag
        if tag == "h6":
            new = create("p", {"role": "heading", "aria-level": "7"})
        else:
            new = create(HEADING_MAP[tag])
        replace_keeping_children(heading, new)


def replace_simple_elements_tag(doc: etree._Element) -> None:
    for target, sources in SIMPLE_TAGS_TO_REPLACE.items():
        for element in list(doc.iter(*sources)):
            replace_keeping_children(element, create(target))


def replace_unknown_elements(doc: etree._Element) -> None:
    body = doc.find("body")
    if body is None:
        return
    for element in list(body.iterdescendants()):
        tag = (element.tag or "").lower()
        if tag and tag not in HTML5_TAGS:
            replace_keeping_children(element, create("div"))


def replace_elements_by_other_with_css(doc: etree._Element) -> None:
    for from_tag, spec in ELEMENTS_WITH_CSS.items():
        style = "".join(f"{key}: {value};" for key, value in spec["properties"].items())
        for element in list(doc.iter(from_tag)):
            new = create(spec["tag"])
            if style:
                new.set("style", style)
            replace_keeping_children(element, new)
