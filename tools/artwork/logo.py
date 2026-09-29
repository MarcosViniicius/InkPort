"""The accent square with one of the panel's icons: the visual signature.

Used by the banner and the social card, so both carry the same mark.
"""

from __future__ import annotations

from tools.artwork import icons, palette
from tools.artwork.svg import Svg


def mark(
    svg: Svg,
    x: float,
    y: float,
    *,
    size: float = 74,
    icon: str = "library",
    icon_ratio: float = 0.62,
) -> None:
    """Rounded accent square at (x, y) with ``icon`` centred in contrast ink."""
    svg.rect(x, y, size, size, rx=size * 0.24, fill=palette.ACCENT)
    inner = size * icon_ratio
    offset = (size - inner) / 2
    svg.raw(
        icons.icon(
            icon,
            x=x + offset,
            y=y + offset,
            size=inner,
            color=palette.ACCENT_CONTRAST,
            width=1.6,
        )
    )
