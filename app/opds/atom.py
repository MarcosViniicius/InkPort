"""Small XML builder helpers for Atom/OPDS documents."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from app.opds.constants import ATOM_NS, DC_NS, OPDS_NS, OPENSEARCH_NS

for prefix, uri in (
    ("", ATOM_NS),
    ("dc", DC_NS),
    ("opds", OPDS_NS),
    ("opensearch", OPENSEARCH_NS),
):
    ET.register_namespace(prefix, uri)


def atom(tag: str) -> str:
    return f"{{{ATOM_NS}}}{tag}"


def dc(tag: str) -> str:
    return f"{{{DC_NS}}}{tag}"


def opds(tag: str) -> str:
    return f"{{{OPDS_NS}}}{tag}"


def make(tag: str, **attrs: str) -> ET.Element:
    element = ET.Element(tag)
    for key, value in attrs.items():
        if value is not None:
            element.set(key.replace("_", "-"), str(value))
    return element


def sub(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    element = make(tag, **attrs)
    if text is not None:
        element.text = text
    parent.append(element)
    return element


def link(
    parent: ET.Element,
    rel: str,
    href: str,
    *,
    type: str | None = None,  # noqa: A002 - matches the Atom attribute name
    title: str | None = None,
    length: int | None = None,
) -> ET.Element:
    return sub(
        parent,
        atom("link"),
        None,
        rel=rel,
        href=href,
        type=type,
        title=title,
        length=length,
    )


def to_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
