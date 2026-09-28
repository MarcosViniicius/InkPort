"""DOM helpers over lxml.

lxml gives us the node operations the reference implementation relies on
(parent/child replacement, attribute editing, serialisation), so the port can
follow the original step by step.
"""

from __future__ import annotations

from lxml import etree, html

try:  # lxml.html.Element produces HtmlElement instances (with text_content()).
    from lxml.html import Element as _html_element  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - very old lxml
    _html_element = None


def parse(data: bytes | str) -> html.HtmlElement:
    """Parse a whole document (head included, so metadata can be read).

    UTF-8 is forced whenever the input is valid UTF-8. libxml2 falls back to
    Latin-1 for HTML with no charset declaration -- which is exactly what an
    inline feed article looks like -- and that silently turned "trás" into
    "trÃ¡s" in every EPUB built from a feed. Only when the bytes are *not*
    valid UTF-8 do we let libxml2 sniff the document's own declaration.
    """
    if isinstance(data, str):
        text = data
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return html.document_fromstring(data)
    return html.document_fromstring(
        text.encode("utf-8"), parser=html.HTMLParser(encoding="utf-8")
    )


def create(tag: str, attrib: dict | None = None) -> etree._Element:
    element = _html_element(tag) if _html_element else etree.Element(tag)
    if attrib:
        for key, value in attrib.items():
            if value is not None:
                element.set(key, str(value))
    return element


def text_content(element: etree._Element | None) -> str:
    """Text of an element regardless of the concrete lxml element class."""
    if element is None:
        return ""
    return "".join(element.itertext())


def remove(element: etree._Element) -> None:
    """Remove an element like DOM's ``element.remove()``, keeping the tail text."""
    parent = element.getparent()
    if parent is None:
        return
    tail = element.tail
    if tail:
        previous = element.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(element)


def replace_keeping_children(element: etree._Element, new: etree._Element) -> None:
    """Swap a tag while preserving attributes, children and surrounding text."""
    for key, value in element.attrib.items():
        new.set(key, value)

    new.text = element.text
    for child in list(element):
        new.append(child)

    parent = element.getparent()
    if parent is None:
        return
    new.tail = element.tail
    parent.replace(element, new)


def strip_tag(element: etree._Element, keep_tail: bool = True) -> None:
    """Drop the tag itself but keep its text and children (moved to the parent)."""
    parent = element.getparent()
    if parent is None:
        return
    index = parent.index(element)
    previous = element.getprevious()

    text = element.text or ""
    if text:
        if previous is not None:
            previous.tail = (previous.tail or "") + text
        else:
            parent.text = (parent.text or "") + text

    for offset, child in enumerate(list(element)):
        parent.insert(index + offset, child)

    if keep_tail and element.tail:
        last = parent[index + len(element) - 1] if len(element) else previous
        if last is not None:
            last.tail = (last.tail or "") + element.tail
        else:
            parent.text = (parent.text or "") + element.tail
    parent.remove(element)


def serialize_xhtml(element: etree._Element, *, pretty: bool = True) -> str:
    """Serialise a fragment as XHTML (void tags self-closed)."""
    content = etree.tostring(element, method="xml", encoding="unicode")
    return content


def inner_xhtml(element: etree._Element) -> str:
    """Serialise the element's children (its inner HTML), as XHTML."""
    parts: list[str] = []
    if element.text:
        parts.append(_escape_text(element.text))
    for child in element:
        parts.append(serialize_xhtml(child))
    return "".join(parts)


def iter_elements(root: etree._Element):
    """All descendant elements, document order."""
    return root.iter()


def text_of(element: etree._Element | None) -> str:
    if element is None:
        return ""
    return text_content(element).strip()


def _escape_text(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
