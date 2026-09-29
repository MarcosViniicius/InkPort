"""The README banner: mark, name, version, tagline, chips and an e-reader."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools.artwork import logo, mockups, palette
from tools.artwork.svg import Svg, text_width

if TYPE_CHECKING:
    from tools.artwork.configuration import Config

WIDTH = 1280
HEIGHT = 300
MARGIN = 80
MARK = 74
TITLE_SIZE = 54
TAGLINE_SIZE = 18
CHIP_HEIGHT = 32
CHIP_GAP = 10
DEVICE_WIDTH = 196
DEVICE_HEIGHT = 208
DEVICE_X = WIDTH - MARGIN - DEVICE_WIDTH
DEVICE_Y = 46


def build(config: Config) -> Svg:
    """Render ``docs/images/banner.svg``."""
    svg = Svg(
        WIDTH,
        HEIGHT,
        title=f"{config.name} — {config.tagline}",
        description="Banner gerado por tools/artwork (rode o gerador após mudar o tema).",
    )
    _background(svg)

    text_x = MARGIN + MARK + 28
    title_y = 140
    logo.mark(svg, MARGIN, 94, size=MARK)
    svg.check_fit(
        config.name,
        size=TITLE_SIZE,
        family="serif",
        available=DEVICE_X - 24 - text_x,
        where="banner title",
    )
    svg.text(text_x, title_y, config.name, size=TITLE_SIZE, fill=palette.TEXT, family="serif", weight=600)
    _version_pill(svg, text_x + text_width(config.name, TITLE_SIZE, "serif") + 26, title_y - 32, config.version)

    svg.check_fit(
        config.tagline,
        size=TAGLINE_SIZE,
        family="sans",
        available=DEVICE_X - 24 - text_x,
        where="banner tagline",
    )
    svg.text(text_x + 2, title_y + 34, config.tagline, size=TAGLINE_SIZE, fill=palette.MUTED)

    _chips(svg, text_x, 198, config.chips, limit=DEVICE_X - 24)

    mockups.device(
        svg,
        DEVICE_X,
        DEVICE_Y,
        width=DEVICE_WIDTH,
        height=DEVICE_HEIGHT,
        rows=7,
        highlight=0,
    )

    svg.text(
        WIDTH - MARGIN,
        HEIGHT - 22,
        f"github.com/{config.repo}" if config.repo else "",
        size=13,
        fill=palette.MUTED,
        family="mono",
        anchor="end",
    )
    return svg


def _background(svg: Svg) -> None:
    svg.rect(0, 0, WIDTH, HEIGHT, fill=palette.BACKGROUND)
    svg.rect(
        0.5,
        0.5,
        WIDTH - 1,
        HEIGHT - 1,
        rx=14,
        fill="none",
        stroke=palette.BORDER,
    )


def _version_pill(svg: Svg, x: float, y: float, version: str) -> None:
    svg.chip(x, y, f"v{version}", size=13, height=28, pad=12)


def _chips(svg: Svg, x: float, y: float, labels: tuple[str, ...], *, limit: float) -> None:
    cursor = x
    for label in labels:
        cursor += svg.chip(cursor, y, label, size=14, height=CHIP_HEIGHT) + CHIP_GAP
    if cursor - CHIP_GAP > limit:
        svg.warn(
            f"banner chips: the row ends at {cursor - CHIP_GAP:.0f}px, past the {limit:.0f}px limit"
        )
