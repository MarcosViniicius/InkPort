"""Link fixing (port of ``fix_links/*.ts``)."""

from __future__ import annotations

import re
from urllib.parse import urldefrag, urljoin

from lxml import etree

from app.converters.webpage.dom import create, inner_xhtml, replace_keeping_children


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


#: Internal reference written by ``_remove_broken_anchor_links``.
_CHAPTER_REF_RE = re.compile(r"^chapter_\d{4}\.xhtml(?:#(?P<anchor>[^#]+))?$")


def remap_chapter_links(chapters: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Retarget internal chapter links once the chapters are consolidated.

    ``fix_links`` runs while the split is still one-file-per-section, so it may
    write ``chapter_0010.xhtml#anchor``. ``consolidate_chapters`` can then merge
    those files away, leaving the link pointing at a chapter that no longer
    exists. Rewrite every reference to the chapter that really owns the anchor:
    a same-file ``#anchor`` when it merged into this one, the other chapter
    otherwise, and drop the href when the anchor is gone altogether.
    """
    if not chapters or not any("chapter_" in body for _title, body in chapters):
        return chapters

    parser = etree.XMLParser(recover=True, resolve_entities=False)
    wrappers: list[etree._Element | None] = []
    for _title, body in chapters:
        try:
            wrappers.append(etree.fromstring(f"<div>{body}</div>".encode(), parser))
        except (etree.XMLSyntaxError, ValueError):
            wrappers.append(None)

    owners: dict[str, int] = {}
    for index, wrapper in enumerate(wrappers):
        if wrapper is None:
            continue
        for element in wrapper.iter():
            for attribute in ("id", "name"):
                value = element.get(attribute)
                if value and value not in owners:
                    owners[value] = index

    remapped: list[tuple[str, str]] = []
    for index, (title, body) in enumerate(chapters):
        wrapper = wrappers[index]
        if wrapper is None:
            remapped.append((title, body))
            continue
        touched = False
        for link in list(wrapper.xpath(".//a[@href]")):
            match = _CHAPTER_REF_RE.match(link.get("href") or "")
            if not match:
                continue
            anchor = match.group("anchor") or ""
            owner = owners.get(anchor) if anchor else None
            if owner is None:
                span = create("span")
                replace_keeping_children(link, span)
                span.attrib.pop("href", None)
            elif owner == index:
                link.set("href", f"#{anchor}")
            else:
                link.set("href", f"chapter_{owner + 1:04d}.xhtml#{anchor}")
            touched = True
        remapped.append((title, inner_xhtml(wrapper)) if touched else (title, body))
    return remapped
