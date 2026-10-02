"""Fonts for the OCR re-lettering mode.

Comic Neue (SIL Open Font License) ships with the project so the rewritten text
works in the slim Docker image, where no system font exists. A user-provided
font path overrides the bundled one.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

VENDOR_FONTS = Path(__file__).resolve().parents[2] / "vendor" / "fonts"
BUNDLED_FONT = VENDOR_FONTS / "ComicNeue-Bold.ttf"

#: System fallbacks, in order, for installations without the bundled font.
FALLBACKS = (
    str(VENDOR_FONTS / "DejaVuSans.ttf"),
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


@lru_cache(maxsize=32)
def load_font(size: int, path: str | None = None):
    """Return ``(font, unicode_ok)``; the fallback font lacks accents."""
    from PIL import ImageFont

    candidates = [path] if path else []
    candidates.append(str(BUNDLED_FONT))
    candidates.extend(FALLBACKS)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ImageFont.truetype(candidate, size), True
        except (OSError, AttributeError):
            continue
    return ImageFont.load_default(), False


def font_path(path: str | None = None) -> str | None:
    """The font file that will actually be used, for logging/diagnostics."""
    for candidate in [path] if path else []:
        if candidate and Path(candidate).exists():
            return candidate
    return str(BUNDLED_FONT) if BUNDLED_FONT.exists() else None
