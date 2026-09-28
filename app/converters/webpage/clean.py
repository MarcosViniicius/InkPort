"""Clean document steps (port of ``clean_document/*.ts``)."""

from __future__ import annotations

import re

from lxml import etree

from app.converters.webpage.dom import remove
from app.converters.webpage.tags import (
    ATTRIBUTES_TO_REMOVE,
    ELEMENTS_TO_REMOVE,
    TAGS_CAN_REMOVE_WHEN_EMPTY,
)

_MULTI_SPACE = re.compile(r"  +")
_MULTI_NEWLINE = re.compile(r"\n[\n ]+")
_SPACE_BEFORE_NEWLINE = re.compile(r" +\n")


def clean_document(doc: etree._Element) -> None:
    remove_elements(doc)
    remove_hidden_elements(doc)
    remove_comments(doc)
    remove_empty_elements(doc)
    remove_data_attributes(doc)
    remove_attributes(doc)
    remove_extra_whitespaces(doc)
    remove_empty_svgs(doc)


def remove_elements(doc: etree._Element) -> None:
    for tag in ELEMENTS_TO_REMOVE:
        for element in list(doc.iter(tag)):
            remove(element)


def remove_hidden_elements(doc: etree._Element) -> None:
    for element in list(doc.xpath("//*[@hidden]")):
        remove(element)


def remove_comments(doc: etree._Element) -> None:
    for comment in list(doc.iter(etree.Comment)):
        remove(comment)


def remove_empty_elements(doc: etree._Element) -> None:
    parents: list[etree._Element] = []
    for element in list(doc.iter()):
        if element.tag in TAGS_CAN_REMOVE_WHEN_EMPTY and _is_empty(element):
            parent = element.getparent()
            remove(element)
            if parent is not None:
                parents.append(parent)
    for parent in parents:
        _remove_empty_upwards(parent)


def _remove_empty_upwards(element: etree._Element | None) -> None:
    while (
        element is not None
        and element.tag in TAGS_CAN_REMOVE_WHEN_EMPTY
        and _is_empty(element)
    ):
        parent = element.getparent()
        remove(element)
        element = parent


def _is_empty(element: etree._Element) -> bool:
    return len(element) == 0 and not (element.text or "").strip()


def remove_data_attributes(doc: etree._Element) -> None:
    for element in doc.iter():
        for name in [key for key in element.attrib if key.startswith("data-")]:
            element.attrib.pop(name, None)


def remove_attributes(doc: etree._Element) -> None:
    for element in doc.iter():
        for name in ATTRIBUTES_TO_REMOVE:
            element.attrib.pop(name, None)


def remove_extra_whitespaces(doc: etree._Element) -> None:
    for element in doc.iter():
        parent_tag = element.getparent().tag if element.getparent() is not None else ""
        if element.tag not in {"pre", "code"}:
            element.text = _clean_whitespaces(element.text)
        if parent_tag not in {"pre", "code"}:
            element.tail = _clean_whitespaces(element.tail)


def _clean_whitespaces(text: str | None) -> str | None:
    if not text:
        return text
    text = text.replace("\r", "").replace("\t", " ")
    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n", text)
    return _SPACE_BEFORE_NEWLINE.sub("\n", text)


def remove_empty_svgs(doc: etree._Element) -> None:
    for svg in list(doc.iter("svg")):
        if len(svg) == 0:
            remove(svg)
    for image in list(doc.xpath('//img[starts-with(@src, "data:image/svg+xml")]')):
        src = image.get("src") or ""
        payload = src.split(",", 1)[-1]
        if "svg" in payload and "<" not in payload:
            remove(image)
