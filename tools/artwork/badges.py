"""Shields-style badges, generated locally (the project uses no CDNs)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools.artwork import palette
from tools.artwork.svg import Svg, num, text_width

if TYPE_CHECKING:
    from tools.artwork.configuration import Badge

HEIGHT = 24
RADIUS = 6
PAD = 10
SIZE = 12
BASELINE = 16


def build(badge: Badge) -> Svg:
    """Render one badge; the width follows the label and value lengths."""
    color = badge.color or palette.ACCENT
    label_width = round(text_width(badge.label, SIZE) + 2 * PAD)
    value_width = round(text_width(badge.value, SIZE) + 2 * PAD)
    width = label_width + value_width

    svg = Svg(
        width,
        HEIGHT,
        title=f"{badge.label}: {badge.value}",
        description="Distintivo gerado por tools/artwork (sem CDN).",
    )
    svg.rect(0, 0, width, HEIGHT, rx=RADIUS, fill=palette.SURFACE, stroke=palette.BORDER)
    # Right half, rounded only on the right: a path keeps the two halves flush.
    svg.raw(
        f'<path d="M{num(label_width)} 0 H{num(width - RADIUS)} '
        f"Q{num(width)} 0 {num(width)} {num(RADIUS)} "
        f"V{num(HEIGHT - RADIUS)} Q{num(width)} {num(HEIGHT)} {num(width - RADIUS)} {num(HEIGHT)} "
        f'H{num(label_width)} Z" fill="{color}"/>'
    )
    svg.text(PAD, BASELINE, badge.label, size=SIZE, fill=palette.MUTED)
    svg.text(label_width + PAD, BASELINE, badge.value, size=SIZE, fill=palette.ink_on(color))
    return svg
