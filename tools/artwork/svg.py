"""Minimal SVG builder: no templating engine and no font metrics.

Everything is emitted with *presentation attributes* (never CSS classes), so a
file renders the same in a browser, on GitHub and inside an ``<img>`` tag.

SVG has no text metrics, so widths are estimated with :func:`text_width`. Every
box checks that its label fits and records a *warning* instead of clipping the
text silently; ``python -m tools.artwork.generate --check`` fails on warnings,
so an overflowing label can never reach the repository.
"""

from __future__ import annotations

from tools.artwork import palette

#: Average glyph advance per character, as a fraction of the font size. Tuned
#: against real renders of the stacks in the stylesheet; intentionally generous
#: (a false "too long" warning is cheaper than clipped text).
FIT_FACTOR = {"sans": 0.63, "serif": 0.62}

#: Monospace fonts have a fixed advance, so no per-character guessing.
MONO_ADVANCE = 0.60

#: Characters clearly narrower than the average proportional glyph.
_NARROW = frozenset("iljtfrI.,:;!'|()[]/- ")


def text_width(value: str, size: float, family: str = "sans") -> float:
    """Estimated width of ``value`` in user units (pixels of the viewBox)."""
    if family == "mono":
        return round(len(value) * size * MONO_ADVANCE, 1)
    factor = FIT_FACTOR.get(family, FIT_FACTOR["sans"])
    units = sum(0.62 if char in _NARROW else 1.0 for char in value)
    return round(units * size * factor, 1)


def esc(value: str) -> str:
    """XML-escape text and attribute values."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def num(value: float) -> str:
    """Compact number (12.0 -> "12"), keeps coordinates readable."""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text or "0"


class Svg:
    """Accumulates a self-contained SVG document."""

    #: Baseline padding before the first text row of a card, and row spacing.
    CARD_TITLE_OFFSET = 32
    CARD_FIRST_ROW = 58
    CARD_ROW_STEP = 20
    CARD_PADDING = 18

    def __init__(self, width: float, height: float, *, title: str, description: str = "") -> None:
        self.width = width
        self.height = height
        self.title = title
        self.description = description
        self.warnings: list[str] = []
        self._defs: list[str] = []
        self._body: list[str] = []
        self._ids: set[str] = set()

    # -- diagnostics -------------------------------------------------------
    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def check_fit(
        self, value: str, *, size: float, family: str, available: float, where: str
    ) -> float:
        """Warn when ``value`` does not fit in ``available`` units."""
        needed = text_width(value, size, family)
        if needed > available:
            self.warn(
                f"{where}: {value!r} needs ~{needed:.0f}px but only {available:.0f}px are available"
            )
        return needed

    # -- primitives --------------------------------------------------------
    def raw(self, markup: str) -> None:
        self._body.append(markup)

    def add_def(self, markup: str) -> None:
        self._defs.append(markup)

    def unique_id(self, prefix: str) -> str:
        index = 0
        while f"{prefix}{index}" in self._ids:
            index += 1
        self._ids.add(f"{prefix}{index}")
        return f"{prefix}{index}"

    def rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        fill: str,
        rx: float = 0,
        stroke: str | None = None,
        stroke_opacity: float | None = None,
        opacity: float | None = None,
    ) -> None:
        parts = [
            f'<rect x="{num(x)}" y="{num(y)}" width="{num(width)}" height="{num(height)}"',
            f'fill="{fill}"',
        ]
        if rx:
            parts.append(f'rx="{num(rx)}"')
        if stroke:
            parts.append(f'stroke="{stroke}"')
            if stroke_opacity is not None:
                parts.append(f'stroke-opacity="{num(stroke_opacity)}"')
        if opacity is not None:
            parts.append(f'opacity="{num(opacity)}"')
        self.raw(" ".join(parts) + "/>")

    def circle(
        self, cx: float, cy: float, r: float, *, fill: str, opacity: float | None = None
    ) -> None:
        extra = f' opacity="{num(opacity)}"' if opacity is not None else ""
        self.raw(f'<circle cx="{num(cx)}" cy="{num(cy)}" r="{num(r)}" fill="{fill}"{extra}/>')

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        stroke: str,
        width: float = 1,
        marker: bool = False,
        dash: str | None = None,
    ) -> None:
        parts = [
            f'<line x1="{num(x1)}" y1="{num(y1)}" x2="{num(x2)}" y2="{num(y2)}"',
            f'stroke="{stroke}" stroke-width="{num(width)}"',
        ]
        if dash:
            parts.append(f'stroke-dasharray="{dash}"')
        if marker:
            self._ensure_arrowhead()
            parts.append('marker-end="url(#arrowhead)"')
        self.raw(" ".join(parts) + "/>")

    def text(
        self,
        x: float,
        y: float,
        value: str,
        *,
        size: float,
        fill: str,
        family: str = "sans",
        weight: int | None = None,
        anchor: str | None = None,
        spacing: float | None = None,
    ) -> None:
        stack = {"sans": palette.SANS, "serif": palette.SERIF, "mono": palette.MONO}[family]
        parts = [
            f'<text x="{num(x)}" y="{num(y)}"',
            f'font-family="{esc(stack)}" font-size="{num(size)}"',
            f'fill="{fill}"',
        ]
        if weight:
            parts.append(f'font-weight="{weight}"')
        if anchor:
            parts.append(f'text-anchor="{anchor}"')
        if spacing is not None:
            parts.append(f'letter-spacing="{num(spacing)}"')
        self.raw(" ".join(parts) + f">{esc(value)}</text>")

    # -- composites --------------------------------------------------------
    def glow(
        self, cx: float, cy: float, r: float, *, color: str | None = None, opacity: float = 0.18
    ) -> None:
        """Soft radial light behind an element."""
        tint = color or palette.ACCENT
        gradient = self.unique_id("glow")
        self.add_def(
            f'<radialGradient id="{gradient}" cx="50%" cy="50%" r="50%">'
            f'<stop offset="0%" stop-color="{tint}" stop-opacity="{num(opacity)}"/>'
            f'<stop offset="100%" stop-color="{tint}" stop-opacity="0"/>'
            "</radialGradient>"
        )
        self.circle(cx, cy, r, fill=f"url(#{gradient})")

    def chip(
        self,
        x: float,
        y: float,
        label: str,
        *,
        size: float = 14,
        height: float = 32,
        pad: float = 16,
        width: float | None = None,
        fill: str | None = None,
        stroke: str | None = None,
        text_fill: str | None = None,
        mono: bool = False,
        where: str = "chip",
    ) -> float:
        """Rounded label; returns its width so callers can lay out a row.

        Pass ``width`` to force a fixed box (grids) instead of hugging the text.
        """
        family = "mono" if mono else "sans"
        if width is None:
            width = round(text_width(label, size, family) + 2 * pad)
        else:
            self.check_fit(
                label, size=size, family=family, available=width - 2 * pad, where=where
            )
        self.rect(
            x,
            y,
            width,
            height,
            rx=min(height / 2, 8),
            fill=fill or palette.SURFACE,
            stroke=stroke or palette.ACCENT,
            stroke_opacity=0.55,
        )
        self.text(
            x + pad,
            y + height / 2 + size * 0.36,
            label,
            size=size,
            fill=text_fill or palette.ACCENT_INK,
            family=family,
        )
        return width

    def card(
        self,
        x: float,
        y: float,
        width: float,
        *,
        title: str | None = None,
        mono: str | None = None,
        lines: tuple[str, ...] | list[str] = (),
        accent: str | None = None,
        height: float | None = None,
        title_size: float = 16,
        row_size: float = 12,
        where: str = "card",
        border: str | None = None,
    ) -> float:
        """Rounded card with a title plus mono/muted/accent rows.

        Returns the height actually used. Every label is checked against the
        inner width, so a long string shows up as a warning, never as clipped
        text.
        """
        rows = (1 if mono else 0) + len(lines) + (1 if accent else 0)
        used = height or (self.CARD_FIRST_ROW + self.CARD_ROW_STEP * (rows - 1) + 22 if rows else 74)
        self.rect(
            x,
            y,
            width,
            used,
            rx=12,
            fill=palette.SURFACE,
            stroke=border or palette.BORDER,
        )
        inner = width - 2 * self.CARD_PADDING
        row_x = x + self.CARD_PADDING

        if title:
            self.check_fit(title, size=title_size, family="sans", available=inner, where=f"{where} title")
            self.text(row_x, y + self.CARD_TITLE_OFFSET, title, size=title_size, fill=palette.TEXT, weight=600)

        cursor = y + self.CARD_FIRST_ROW
        if mono:
            self.check_fit(mono, size=row_size, family="mono", available=inner, where=f"{where} mono")
            self.text(row_x, cursor, mono, size=row_size, fill=palette.ACCENT_INK, family="mono")
            cursor += self.CARD_ROW_STEP
        for line in lines:
            self.check_fit(line, size=row_size, family="sans", available=inner, where=f"{where} line")
            self.text(row_x, cursor, line, size=row_size, fill=palette.MUTED)
            cursor += self.CARD_ROW_STEP
        if accent:
            self.check_fit(accent, size=row_size, family="sans", available=inner, where=f"{where} accent")
            self.text(row_x, cursor, accent, size=row_size, fill=palette.ACCENT)
        return used

    def arrow_down(
        self, x: float, y_from: float, y_to: float, *, label: str | None = None
    ) -> None:
        """Vertical connector with an arrowhead and an optional side label."""
        self.line(x, y_from, x, y_to, stroke=palette.ACCENT, width=2, marker=True)
        if label:
            self.text(x + 16, (y_from + y_to) / 2 + 4, label, size=12, fill=palette.MUTED)

    # -- document ----------------------------------------------------------
    def render(self) -> str:
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{num(self.width)}" '
                f'height="{num(self.height)}" viewBox="0 0 {num(self.width)} {num(self.height)}" '
                f'role="img" aria-label="{esc(self.title)}">'
            ),
            f"  <title>{esc(self.title)}</title>",
        ]
        if self.description:
            parts.append(f"  <desc>{esc(self.description)}</desc>")
        if self._defs:
            parts.append("  <defs>\n    " + "\n    ".join(self._defs) + "\n  </defs>")
        for chunk in self._body:
            parts.append(f"  {chunk}")
        parts.append("</svg>\n")
        return "\n".join(parts)

    def _ensure_arrowhead(self) -> None:
        if "arrowhead" in self._ids:
            return
        self._ids.add("arrowhead")
        self.add_def(
            '<marker id="arrowhead" viewBox="0 0 10 10" refX="8.5" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0 0 L10 5 L0 10 z" fill="{palette.ACCENT}"/></marker>'
        )
