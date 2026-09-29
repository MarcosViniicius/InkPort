"""Architecture diagram, laid out from the declarative structure in artwork.toml.

Each ``[[diagram.tier]]`` is one horizontal row: a label, an optional band, and
either a row of cards (``kind = "cards"``, the default) or a grid of monospace
chips (``kind = "chips"``). ``arrows`` are the connectors drawn between
consecutive tiers, in order.

Heights come from the content, so adding a line or a card never means editing
coordinates. The layout runs in two phases -- :func:`plan` (pure geometry) and
:func:`_draw` -- which keeps the vertical maths testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tools.artwork import palette
from tools.artwork.svg import Svg

if TYPE_CHECKING:
    from tools.artwork.configuration import Config

DEFAULT_WIDTH = 1200
MARGIN = 60
GAP = 30
BAND_PAD = 22
LABEL_STEP = 22
ARROW_GAP = 56
ARROW_INSET = 10
CHIP_COLUMNS = 3
CHIP_GAP = 14
CHIP_HEIGHT = 34
TOP_MARGIN = 26
BOTTOM_MARGIN = 26


@dataclass
class Placement:
    """Where one tier goes (all numbers are absolute y coordinates)."""

    tier: dict
    label_y: float
    cards_top: float
    card_height: float
    band_top: float | None = None
    band_height: float | None = None
    arrow: tuple[float, float] | None = None
    arrow_label: str | None = None
    cards: list[dict] = field(default_factory=list)


def build(config: Config) -> Svg:
    """Render ``docs/images/architecture.svg``."""
    spec = config.diagram or {}
    tiers = spec.get("tier") or []
    arrows = list(spec.get("arrows") or [])
    width = int(spec.get("width", DEFAULT_WIDTH))

    placements, height = plan(tiers, arrows)
    svg = Svg(
        width,
        height,
        title=str(spec.get("title") or f"Arquitetura do {config.name}"),
        description="Diagrama gerado por tools/artwork a partir de artwork.toml.",
    )
    svg.rect(0, 0, width, height, fill=palette.BACKGROUND)
    for placement in placements:
        _draw(svg, placement, width)
    return svg


def plan(tiers: list[dict], arrows: list[str]) -> tuple[list[Placement], float]:
    """Compute every y coordinate; returns the placements and the total height."""
    placements: list[Placement] = []
    y = float(TOP_MARGIN)
    for index, tier in enumerate(tiers):
        banded = bool(tier.get("band"))
        card_height = _content_height(tier)
        label_y = y + 12
        cards_top = y + LABEL_STEP + (BAND_PAD if banded else 0)
        bottom = cards_top + card_height + (BAND_PAD if banded else 0)

        placement = Placement(
            tier=tier,
            label_y=label_y,
            cards_top=cards_top,
            card_height=card_height,
            band_top=y + LABEL_STEP if banded else None,
            band_height=(bottom - (y + LABEL_STEP)) if banded else None,
            cards=list(tier.get("cards") or []),
        )
        if index < len(tiers) - 1:
            placement.arrow = (bottom + ARROW_INSET, bottom + ARROW_GAP - ARROW_INSET)
            placement.arrow_label = arrows[index] if index < len(arrows) else None
            bottom += ARROW_GAP
        placements.append(placement)
        y = bottom
    return placements, y + BOTTOM_MARGIN


def _content_height(tier: dict) -> float:
    if tier.get("kind") == "chips":
        rows = math.ceil(len(tier.get("chips") or []) / CHIP_COLUMNS) or 1
        return rows * CHIP_HEIGHT + (rows - 1) * CHIP_GAP
    cards = tier.get("cards") or []
    rows = max((_rows(card) for card in cards), default=1)
    return Svg.CARD_FIRST_ROW + Svg.CARD_ROW_STEP * (rows - 1) + 22


def _rows(card: dict) -> int:
    return (
        (1 if card.get("mono") else 0)
        + len(card.get("lines") or [])
        + (1 if card.get("accent") else 0)
    ) or 1


def _draw(svg: Svg, placement: Placement, width: float) -> None:
    tier = placement.tier
    label = str(tier.get("label") or "")
    if label:
        svg.text(MARGIN, placement.label_y, label.upper(), size=12, fill=palette.ACCENT, spacing=1.6)

    start, end = _content_span(bool(tier.get("band")), width)
    if placement.band_top is not None:
        svg.rect(
            MARGIN,
            placement.band_top,
            width - 2 * MARGIN,
            placement.band_height or 0,
            rx=16,
            fill=palette.BAND,
            stroke=palette.BORDER,
        )

    if tier.get("kind") == "chips":
        _draw_chips(svg, tier, placement, start, end)
    else:
        _draw_cards(svg, tier, placement, start, end)

    if placement.arrow:
        svg.arrow_down(
            width / 2,
            placement.arrow[0],
            placement.arrow[1],
            label=placement.arrow_label,
        )


def _content_span(banded: bool, width: float) -> tuple[float, float]:
    inset = BAND_PAD if banded else 0
    return MARGIN + inset, width - MARGIN - inset


def _draw_cards(svg: Svg, tier: dict, placement: Placement, start: float, end: float) -> None:
    cards = placement.cards
    if not cards:
        return
    card_width = (end - start - GAP * (len(cards) - 1)) / len(cards)
    where = label_hint(tier)
    for index, card in enumerate(cards):
        x = start + index * (card_width + GAP)
        svg.card(
            x,
            placement.cards_top,
            card_width,
            title=card.get("title"),
            mono=card.get("mono"),
            lines=tuple(card.get("lines") or ()),
            accent=card.get("accent"),
            height=placement.card_height,
            border=palette.BORDER_STRONG if card.get("highlight") else palette.BORDER,
            where=f"{where} card {index + 1}",
        )


def _draw_chips(svg: Svg, tier: dict, placement: Placement, start: float, end: float) -> None:
    chips = list(tier.get("chips") or [])
    if not chips:
        return
    columns = min(CHIP_COLUMNS, len(chips))
    chip_width = (end - start - CHIP_GAP * (columns - 1)) / columns
    where = label_hint(tier)
    for index, label in enumerate(chips):
        row, column = divmod(index, columns)
        svg.chip(
            start + column * (chip_width + CHIP_GAP),
            placement.cards_top + row * (CHIP_HEIGHT + CHIP_GAP),
            str(label),
            size=14,
            height=CHIP_HEIGHT,
            width=chip_width,
            mono=True,
            where=f"{where} chip",
        )


def label_hint(tier: dict) -> str:
    """Short prefix for fit warnings (``"NÚCLEO card 2"``)."""
    return str(tier.get("label") or "tier")[:18]
