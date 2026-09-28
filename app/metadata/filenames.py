"""Filename heuristics: guess title, author and series/volume from a name.

Files arriving from the wild are rarely tagged. This module extracts what it
can so imports look sensible even before any metadata is edited.
"""

from __future__ import annotations

import re

_VOLUME_PATTERNS = (
    # "Series Name v01" / "Series Name Vol. 2" / "Series Name Volume 3"
    re.compile(
        r"^(?P<title>.+?)[\s\-_]+(?:v|vol\.?|volume)[\s\.]*0*(?P<idx>\d+(?:\.\d+)?)$",
        re.I,
    ),
    # "Series - 01 - Subtitle"
    re.compile(r"^(?P<title>.+?)[\s\-_]+0*(?P<idx>\d{1,3})[\s\-_]+(?P<rest>.+)$"),
    # "Series Name 001"
    re.compile(r"^(?P<title>.+?)[\s\-_]+0*(?P<idx>\d{2,4})(?:[\s\-_].*)?$"),
)

_PARENTHESIS_AUTHOR = re.compile(r"\(([^()]{3,60})\)\s*$")
_YEAR = re.compile(r"[\(\[](\d{4})[\)\]]")
_LEADING_GROUP = re.compile(r"^\[[^\]]+\]\s*")
_WHITESPACE = re.compile(r"[_\s]+")


def parse_filename(stem: str) -> dict:
    original = stem
    stem = _LEADING_GROUP.sub("", stem).strip()

    author = None
    paren = _PARENTHESIS_AUTHOR.search(stem)
    if paren and not paren.group(1).isdigit():
        author = paren.group(1).strip()
        stem = stem[: paren.start()].strip()

    year = _YEAR.search(original)

    series = None
    index = None
    title = stem
    for pattern in _VOLUME_PATTERNS:
        match = pattern.match(stem)
        if not match:
            continue
        groups = match.groupdict()
        base = groups.get("title", stem).strip(" -_.")
        index = _to_float(groups.get("idx"))
        if index is not None:
            series = base
            rest = groups.get("rest")
            title = f"{base} {int(index)} - {rest}" if rest else f"{base} {_fmt(index)}"
        else:
            title = base
        break

    title = _WHITESPACE.sub(" ", title).strip(" -_.")
    return {
        "title": title or original,
        "author": author,
        "series": series,
        "series_index": index,
        "published": year.group(1) if year else None,
    }


def _to_float(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def _fmt(index: float) -> str:
    return f"{int(index):02d}" if index == int(index) else str(index)
