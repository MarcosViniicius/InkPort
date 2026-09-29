"""Social card (1200x630): the image to paste when sharing the project."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools.artwork import logo, mockups, palette
from tools.artwork.svg import Svg, text_width

if TYPE_CHECKING:
    from tools.artwork.configuration import Config

WIDTH = 1200
HEIGHT = 630
MARGIN = 80
MARK = 84
DEVICE_WIDTH = 220
DEVICE_HEIGHT = 400
DEVICE_X = WIDTH - MARGIN - DEVICE_WIDTH
DEVICE_Y = 115
TEXT_LIMIT = DEVICE_X - 24
HEADLINE_SIZE = 52
#: Vertical rhythm. The formats row is anchored to the bottom (above the footer)
#: and the generator warns if the bullets would ever reach it.
NAME_BASELINE = 118
HEADLINE_TOP = 226
HEADLINE_STEP = 60
BULLET_SIZE = 17
BULLET_TOP = 350
BULLET_STEP = 32
BULLET_GAP = 24
CHIP_HEIGHT = 34
FOOTER_BASELINE = HEIGHT - 42
FORMATS_Y = FOOTER_BASELINE - CHIP_HEIGHT - 26


def build(config: Config) -> Svg:
    """Render ``docs/images/social-card.svg``."""
    svg = Svg(
        WIDTH,
        HEIGHT,
        title=f"{config.name} — {config.tagline}",
        description="Cartão social gerado por tools/artwork.",
    )
    svg.rect(0, 0, WIDTH, HEIGHT, fill=palette.BACKGROUND)
    svg.rect(0.5, 0.5, WIDTH - 1, HEIGHT - 1, rx=16, fill="none", stroke=palette.BORDER)
    svg.glow(DEVICE_X + DEVICE_WIDTH / 2, DEVICE_Y + DEVICE_HEIGHT / 2, 330, opacity=0.16)

    logo.mark(svg, MARGIN, 72, size=MARK)
    name_x = MARGIN + MARK + 24
    svg.text(name_x, NAME_BASELINE, config.name, size=30, fill=palette.TEXT, weight=600)
    svg.chip(
        name_x + text_width(config.name, 30) + 16,
        100,
        f"v{config.version}",
        size=13,
        height=28,
        pad=12,
    )

    _headline(svg, config)
    bullets_end = _bullets(svg, config)
    _formats(svg, config, y=FORMATS_Y)
    if bullets_end > FORMATS_Y - 16:
        svg.warn(
            f"social bullets: end at {bullets_end:.0f}px but the formats row starts at "
            f"{FORMATS_Y:.0f}px"
        )

    mockups.device(
        svg,
        DEVICE_X,
        DEVICE_Y,
        width=DEVICE_WIDTH,
        height=DEVICE_HEIGHT,
        rows=9,
        highlight=1,
        rule_after=4,
    )
    svg.text(
        MARGIN,
        FOOTER_BASELINE,
        f"github.com/{config.repo}" if config.repo else "",
        size=13,
        fill=palette.MUTED,
        family="mono",
    )
    return svg


def _headline(svg: Svg, config: Config) -> None:
    cursor = HEADLINE_TOP
    for line in config.headline:
        svg.check_fit(
            line,
            size=HEADLINE_SIZE,
            family="serif",
            available=TEXT_LIMIT - MARGIN,
            where="social headline",
        )
        svg.text(MARGIN, cursor, line, size=HEADLINE_SIZE, fill=palette.TEXT, family="serif", weight=600)
        cursor += HEADLINE_STEP
    last_baseline = cursor - HEADLINE_STEP
    if last_baseline > BULLET_TOP - 20:
        svg.warn(f"social headline: a última linha desce até {last_baseline:.0f}px, colada nos bullets")


def _bullets(svg: Svg, config: Config) -> float:
    """Draw the bullets; returns the y where the list ends."""
    cursor = BULLET_TOP
    for bullet in config.bullets:
        svg.check_fit(
            bullet,
            size=BULLET_SIZE,
            family="sans",
            available=TEXT_LIMIT - MARGIN - 24,
            where="social bullet",
        )
        svg.circle(MARGIN + 5, cursor - 5, 4, fill=palette.ACCENT)
        svg.text(MARGIN + 24, cursor, bullet, size=BULLET_SIZE, fill=palette.MUTED)
        cursor += BULLET_STEP
    return cursor - BULLET_STEP + BULLET_GAP


def _formats(svg: Svg, config: Config, *, y: float) -> None:
    cursor = MARGIN
    for label in config.formats:
        cursor += svg.chip(cursor, y, label, size=14, height=CHIP_HEIGHT) + 10
    if cursor - 10 > TEXT_LIMIT:
        svg.warn(f"social formats: the row ends at {cursor - 10:.0f}px, past the {TEXT_LIMIT:.0f}px limit")
