"""Split the main content by headings (port of ``split_main_content.ts``).

Each ``<h2>`` (or ``h2``/``h3`` when there is only one ``h2``) starts a chapter;
whatever is left with enough text becomes a final chapter named after the page.

A pointer to the reference: splitting by every heading produces a chapter per
``<h2>``, which for an article means many one-paragraph "pages" on the reader.
``consolidate_chapters`` merges them afterwards -- see its docstring.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

from lxml import etree

from app.converters.webpage.dom import create, remove
from app.converters.webpage.tags import REMAINING_TEXT_LIMIT

_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

#: Sections shorter than this are merged into the previous chapter.
MIN_CHAPTER_CHARS = 6000
#: A chapter grows until about this size before a new one starts.
TARGET_CHAPTER_CHARS = 12000
#: An article with less text than this becomes a single chapter. Blog posts are
#: one readable unit; turning every <h2> into a "page" makes a useless table of
#: contents on the reader.
SINGLE_CHAPTER_CHARS = 35000


def split_main_content(
    main_content: etree._Element, title: str
) -> list[tuple[str, etree._Element]]:
    headings_h2 = [h for h in main_content.iter("h2") if _has_title(h)]
    if not headings_h2:
        return [(title, main_content)]

    if len(headings_h2) > 1:
        references = headings_h2
    else:
        references = [
            element
            for element in main_content.iter()
            if element.tag in {"h2", "h3"} and _has_title(element)
        ]

    return _split(main_content, title, references)


def _split(
    main_content: etree._Element, title: str, references: list[etree._Element]
) -> list[tuple[str, etree._Element]]:
    chapters: list[tuple[str, etree._Element]] = []

    for reference in reversed(references):
        ancestors: list[etree._Element] = []
        node = reference
        while node is not None and node is not main_content:
            ancestors.append(node)
            node = node.getparent()

        new_root = create("div")
        chapter_title = _recursive_split(ancestors, new_root)
        if (new_root.text_content() or "").strip():
            chapters.append((chapter_title, new_root))

    remaining = (main_content.text_content() or "").strip()
    if len(remaining) >= REMAINING_TEXT_LIMIT:
        new_root = create("div")
        _move_all(main_content, new_root)
        if (new_root.text_content() or "").strip():
            chapters.append((title, new_root))

    chapters.reverse()
    return chapters


def _recursive_split(
    ancestors: list[etree._Element], new_parent: etree._Element
) -> str:
    remaining = list(ancestors)
    element = remaining.pop()
    current_parent = element.getparent()

    title = ""
    if remaining:
        clone = _clone_element_only(element)
        title = _recursive_split(remaining, clone)
        new_parent.append(clone)

    _move_after(current_parent, new_parent, element)

    if not remaining:
        title = (element.text_content() or "").strip()
        remove(element)

    return title


def _move_after(
    current_parent: etree._Element | None,
    new_parent: etree._Element,
    reference: etree._Element,
) -> None:
    if current_parent is None:
        return
    siblings = list(current_parent)
    if reference not in siblings:
        return
    index = siblings.index(reference)
    tail = reference.tail
    existing = list(new_parent)

    for node in siblings[index + 1:]:
        current_parent.remove(node)
        new_parent.append(node)

    if tail:
        if existing:
            last = existing[-1]
            last.tail = (last.tail or "") + tail
        else:
            new_parent.text = (new_parent.text or "") + tail


def _move_all(current_parent: etree._Element, new_parent: etree._Element) -> None:
    new_parent.text = current_parent.text
    for node in list(current_parent):
        current_parent.remove(node)
        new_parent.append(node)


def _clone_element_only(element: etree._Element) -> etree._Element:
    clone = create(element.tag)
    for key, value in element.attrib.items():
        clone.set(key, value)
    return clone


def _has_title(heading: etree._Element) -> bool:
    return bool((heading.text_content() or "").strip())


def visible_length(xhtml: str) -> int:
    """Number of readable characters in an XHTML fragment."""
    return len(_WS.sub(" ", _TAGS.sub(" ", xhtml)).strip())


def _demoted_heading(title: str) -> str:
    """A merged section keeps its heading inside the chapter body.

    ``split_main_content`` removes the heading element and returns its text as
    the chapter title. When sections are merged that title would be lost, so it
    comes back as an ``<h2>`` -- otherwise the article silently loses its
    subheadings (a fidelity problem for a reader).
    """
    clean = (title or "").strip()
    if not clean:
        return ""
    return f"<h2>{escape(clean)}</h2>"


def consolidate_chapters(
    chapters: list[tuple[str, str]],
    *,
    min_chars: int = MIN_CHAPTER_CHARS,
    target_chars: int = TARGET_CHAPTER_CHARS,
    single_below: int = SINGLE_CHAPTER_CHARS,
) -> list[tuple[str, str]]:
    """Turn heading-split sections into reader-friendly chapters.

    Splitting one article by every heading gives many tiny chapters, which is
    bad on an e-reader (long, useless table of contents). So:

    * a short article becomes a **single** chapter;
    * small sections are merged into the previous chapter until it reaches a
      reasonable size, so a long article keeps a few substantial chapters.

    When sections are merged, the absorbed section's heading is kept in the body
    (see ``_demoted_heading``) instead of disappearing.
    """
    if len(chapters) <= 1:
        return chapters

    total = sum(visible_length(body) for _title, body in chapters)
    if total < single_below:
        parts: list[str] = []
        for index, (title, body) in enumerate(chapters):
            if index:
                heading = _demoted_heading(title)
                if heading:
                    parts.append(heading)
            parts.append(body)
        return [(chapters[0][0], "\n".join(parts))]

    merged: list[tuple[str, str]] = []
    for title, body in chapters:
        if (
            merged
            and visible_length(body) < min_chars
            and visible_length(merged[-1][1]) < target_chars
        ):
            previous_title, previous_body = merged[-1]
            heading = _demoted_heading(title)
            merged[-1] = (
                previous_title,
                f"{previous_body}\n{heading}\n{body}" if heading else f"{previous_body}\n{body}",
            )
        else:
            merged.append((title, body))

    # A tiny opening section belongs with the next one.
    if len(merged) > 1 and visible_length(merged[0][1]) < min_chars:
        _first_title, first_body = merged.pop(0)
        next_title, next_body = merged[0]
        merged[0] = (next_title, f"{first_body}\n{next_body}")

    return merged or chapters
