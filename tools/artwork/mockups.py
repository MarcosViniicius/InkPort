"""Reusable mockups for the artwork (an e-ink screen and a book block).

Mockups are decoration: they must never contain real content, only abstract
bars, so the art stays honest and cannot go stale.
"""

from __future__ import annotations

from tools.artwork import palette
from tools.artwork.svg import Svg


def device(
    svg: Svg,
    x: float,
    y: float,
    *,
    width: float = 196,
    height: float = 208,
    rows: int = 7,
    highlight: int | None = 0,
    rule_after: int | None = None,
    radius: float = 18,
) -> None:
    """An e-ink reader: frame, recessed screen and abstract text lines."""
    svg.rect(
        x, y, width, height, rx=radius, fill=palette.SURFACE, stroke=palette.BORDER
    )
    pad = width * 0.08
    screen_x = x + pad
    screen_y = y + pad
    screen_w = width - 2 * pad
    screen_h = height - 2 * pad
    svg.rect(
        screen_x, screen_y, screen_w, screen_h, rx=8, fill=palette.INSET
    )

    bar_x = screen_x + 18
    bar_w = screen_w - 36
    widths = (1.0, 0.86, 0.94, 0.72, 0.9, 0.8, 0.6)
    step = max(14.0, screen_h / (rows + 3))
    cursor = screen_y + 22
    for index in range(rows):
        if rule_after is not None and index == rule_after:
            svg.rect(bar_x, cursor - 8, bar_w, 1, fill=palette.BAR)
            cursor += 12
        ratio = widths[index % len(widths)]
        color = palette.ACCENT if index == highlight else palette.BAR
        svg.rect(bar_x, cursor, bar_w * ratio, 7, rx=3.5, fill=color)
        cursor += step


def cover_stack(svg: Svg, x: float, y: float, *, width: float = 96, height: float = 132) -> None:
    """Three offset book covers, the back ones dimmed."""
    for index, opacity in ((2, 0.25), (1, 0.5)):
        svg.rect(
            x + index * 16,
            y - index * 12,
            width,
            height,
            rx=10,
            fill=palette.BORDER_STRONG,
            opacity=opacity,
        )
    svg.rect(
        x,
        y,
        width,
        height,
        rx=10,
        fill=palette.ACCENT_SOFT,
        stroke=palette.BORDER_STRONG,
    )
    svg.rect(x + 14, y + 20, width - 28, 8, rx=4, fill=palette.ACCENT)
    svg.rect(x + 14, y + 40, width - 42, 6, rx=3, fill=palette.BAR)
    svg.rect(x + 14, y + 54, width - 34, 6, rx=3, fill=palette.BAR)
