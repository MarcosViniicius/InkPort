"""Icon paths reused from the panel's icon set (single source of truth).

The panel renders its icons from ``app/web/templates/partials/icons.html``.
Instead of copying that dictionary here, we parse it: one place to edit, and the
artwork can never show an outdated icon. Use :func:`names` to list what is
available.
"""

from __future__ import annotations

import re

from tools.artwork import palette

PARTIAL = palette.ROOT / "app" / "web" / "templates" / "partials" / "icons.html"
_ENTRY = re.compile(r"'([a-z0-9]+)':\s*'([^']*)'")


class IconError(RuntimeError):
    """Raised when the icon partial is missing or lacks a requested icon."""


def paths() -> dict[str, str]:
    """``name -> inner SVG markup`` for every icon in the panel's set."""
    if not PARTIAL.is_file():
        raise IconError(f"icon partial not found: {PARTIAL}")
    found = dict(_ENTRY.findall(PARTIAL.read_text("utf-8")))
    if not found:
        raise IconError(f"no icons parsed from {PARTIAL.name}; the format changed")
    return found


def names() -> list[str]:
    """Available icon names (handy when extending the artwork)."""
    return sorted(paths())


def icon(
    name: str,
    *,
    x: float,
    y: float,
    size: float,
    color: str,
    width: float = 1.7,
) -> str:
    """One icon scaled into a ``size`` x ``size`` box whose top-left is (x, y).

    The source set is a 24x24 viewBox drawn with ``currentColor``; a nested
    ``<svg>`` keeps that geometry and re-colours it with ``color``.
    """
    catalogue = paths()
    if name not in catalogue:
        raise IconError(f"unknown icon {name!r}; available: {', '.join(sorted(catalogue))}")
    return (
        f'<svg x="{_n(x)}" y="{_n(y)}" width="{_n(size)}" height="{_n(size)}" '
        'viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{width}" stroke-linecap="round" '
        f'stroke-linejoin="round">{catalogue[name]}</svg>'
    )


def _n(value: float) -> str:
    return f"{value:g}"
