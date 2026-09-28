"""Link fixing (port of ``fix_links/*.ts``)."""

from __future__ import annotations

from urllib.parse import urldefrag, urljoin

from lxml import etree

from app.converters.webpage.dom import create, replace_keeping_children


def fix_links(chapters: list[tuple[str, etree._Element]], page_url: str) -> None:
    elements = [element for _title, element in chapters]
    _replace_url_links(elements, page_url)
    _set_external_links_blank(elements)
    _remove_broken_anchor_links(elements)


def _replace_url_links(elements: list[etree._Element], page_url: str) -> None:
    for link in _links(elements):
        href = link.get("href") or ""
        absolute = urljoin(page_url, href)
        base, _fragment = urldefrag(absolute)
        page_base, _page_fragment = urldefrag(page_url)
        if base == page_base:
            fragment = urldefrag(absolute)[1]
            link.set("href", f"#{fragment}" if fragment else absolute)
        else:
            link.set("href", absolute)


def _set_external_links_blank(elements: list[etree._Element]) -> None:
    for link in _links(elements):
        href = link.get("href") or ""
        if href.startswith(("http://", "https://")):
            link.set("target", "_blank")


def _remove_broken_anchor_links(elements: list[etree._Element]) -> None:
    for index, element in enumerate(elements):
        for link in list(element.xpath('.//a[starts-with(@href, "#")]')):
            anchor = (link.get("href") or "")[1:]
            target = _index_of_anchor(elements, anchor)
            if target == -1:
                span = create("span")
                replace_keeping_children(link, span)
                span.attrib.pop("href", None)
            elif target != index:
                link.set("href", f"chapter_{target + 1:04d}.xhtml#{anchor}")


def _index_of_anchor(elements: list[etree._Element], anchor: str) -> int:
    if not anchor:
        return -1
    for index, element in enumerate(elements):
        found = element.xpath(
            f'.//*[@id="{_escape(anchor)}"] | .//a[@name="{_escape(anchor)}"]'
        )
        if found:
            return index
    return -1


def _escape(value: str) -> str:
    return value.replace('"', "")


def _links(elements: list[etree._Element]):
    for element in elements:
        yield from element.xpath(".//a[@href]")
