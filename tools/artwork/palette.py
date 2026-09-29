"""Design tokens for the artwork, read from the panel's own stylesheet.

Nothing here is hard-coded: colours and font stacks are parsed from
``app/web/static/style.css`` (dark-theme declarations come last, so they win --
the artwork is dark, like the screenshots). Change the theme, regenerate the
art, done: the images can never drift from the UI.

Derived shades (band backgrounds, mockup bars) come from :func:`mix`, so they
follow any theme change too.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STYLESHEET = ROOT / "app" / "web" / "static" / "style.css"

#: Artwork token -> CSS custom property. Change the right-hand side if the
#: stylesheet renames a variable; everything else keeps working.
TOKENS = {
    "background": "paper",
    "surface": "surface",
    "border": "border",
    "border_strong": "border-strong",
    "text": "ink",
    "muted": "muted",
    "accent": "accent",
    "accent_ink": "accent-ink",
    "accent_soft": "accent-soft",
    "accent_contrast": "accent-contrast",
    "ok": "ok",
    "warn": "warn",
    "danger": "danger",
}

_FALLBACK_FONTS = {
    "sans": "ui-sans-serif, system-ui, 'Segoe UI', Roboto, sans-serif",
    "serif": "Georgia, 'Times New Roman', serif",
    "mono": "ui-monospace, Consolas, 'Courier New', monospace",
}

_DECLARATION = re.compile(r"--([a-zA-Z0-9-]+)\s*:\s*([^;{}]+);", re.DOTALL)
_HEX = re.compile(r"#[0-9a-fA-F]{3,8}")


class PaletteError(RuntimeError):
    """Raised when the stylesheet no longer provides an expected token."""


def _declarations() -> dict[str, str]:
    """Every ``--custom-property: value`` in the stylesheet (last one wins)."""
    if not STYLESHEET.is_file():
        raise PaletteError(f"stylesheet not found: {STYLESHEET}")
    text = STYLESHEET.read_text(encoding="utf-8")
    return {name: value.strip() for name, value in _DECLARATION.findall(text)}


def declarations() -> dict[str, str]:
    """Public view of the stylesheet tokens (used by tests and tooling)."""
    return _declarations()


def _color(name: str, css_name: str, values: dict[str, str]) -> str:
    raw = values.get(css_name, "")
    match = _HEX.search(raw)
    if not match:
        raise PaletteError(
            f"colour token '{name}' needs --{css_name} to be a hex colour in "
            f"{STYLESHEET.name} (found {raw!r})"
        )
    return match.group(0).lower()


def _font(name: str, values: dict[str, str]) -> str:
    """Font stack ready for an SVG attribute (single-quoted, single line)."""
    raw = values.get(name) or _FALLBACK_FONTS[name]
    collapsed = " ".join(raw.split())
    return collapsed.replace('"', "'").rstrip(",")


_VALUES = _declarations()

#: Colours (named after the artwork's intent, not the CSS variable).
BACKGROUND = _color("background", TOKENS["background"], _VALUES)
SURFACE = _color("surface", TOKENS["surface"], _VALUES)
BORDER = _color("border", TOKENS["border"], _VALUES)
BORDER_STRONG = _color("border_strong", TOKENS["border_strong"], _VALUES)
TEXT = _color("text", TOKENS["text"], _VALUES)
MUTED = _color("muted", TOKENS["muted"], _VALUES)
ACCENT = _color("accent", TOKENS["accent"], _VALUES)
ACCENT_INK = _color("accent_ink", TOKENS["accent_ink"], _VALUES)
ACCENT_SOFT = _color("accent_soft", TOKENS["accent_soft"], _VALUES)
ACCENT_CONTRAST = _color("accent_contrast", TOKENS["accent_contrast"], _VALUES)
OK = _color("ok", TOKENS["ok"], _VALUES)
WARN = _color("warn", TOKENS["warn"], _VALUES)
DANGER = _color("danger", TOKENS["danger"], _VALUES)

#: Font stacks for the SVG ``font-family`` attribute.
SANS = _font("sans", _VALUES)
SERIF = _font("serif", _VALUES) if "serif" in _VALUES else _FALLBACK_FONTS["serif"]
MONO = _font("mono", _VALUES)


def mix(first: str, second: str, ratio: float) -> str:
    """Blend two hex colours. ``ratio`` 0 -> first, 1 -> second."""
    a = _rgb(first)
    b = _rgb(second)
    blended = tuple(round(x + (y - x) * ratio) for x, y in zip(a, b, strict=True))
    return "#" + "".join(f"{value:02x}" for value in blended)


def _rgb(value: str) -> tuple[int, int, int]:
    raw = value.lstrip("#")
    if len(raw) == 3:
        raw = "".join(char * 2 for char in raw)
    if len(raw) not in (6, 8):
        raise PaletteError(f"not a hex colour: {value!r}")
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def rgb(value: str) -> tuple[int, int, int]:
    """Public wrapper used by callers that need the channels."""
    return _rgb(value)


def luminance(value: str) -> float:
    """Perceptual luminance (0 = black, 1 = white)."""
    red, green, blue = _rgb(value)
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255


def ink_on(background: str) -> str:
    """Readable text colour for a filled shape (dark on light, light on dark)."""
    return ACCENT_CONTRAST if luminance(background) > 0.55 else "#ffffff"


#: Large bands behind a group of cards (a shade lighter than the background).
BAND = mix(BACKGROUND, SURFACE, 0.45)
#: Device screens and other recessed areas (darker than the background).
INSET = mix(BACKGROUND, "#000000", 0.35)
#: Fake text bars inside mockups.
BAR = mix(SURFACE, BORDER_STRONG, 0.55)

__all__ = [
    "ACCENT",
    "ACCENT_CONTRAST",
    "ACCENT_INK",
    "ACCENT_SOFT",
    "BACKGROUND",
    "BAND",
    "BAR",
    "BORDER",
    "BORDER_STRONG",
    "DANGER",
    "INSET",
    "MONO",
    "MUTED",
    "OK",
    "PaletteError",
    "ROOT",
    "SANS",
    "SERIF",
    "STYLESHEET",
    "SURFACE",
    "TEXT",
    "TOKENS",
    "WARN",
    "mix",
]
