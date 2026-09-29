"""Generated covers: accented text must render as letters, never as boxes.

The slim Docker image has no system font, so Pillow fell back to its built-in
font -- which has no ``é ç ã`` glyphs and drew tofu. The project now ships
DejaVu Sans so the typographic cover is readable in every installation.

Run with:  python tests/covers.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def main() -> int:
    from app.metadata.cover import (
        BUNDLED_FONT,
        _ascii_fold,
        _load_font,
        generate_title_cover,
    )

    print("\n[fonte embutida]")
    check("a fonte do projeto existe", BUNDLED_FONT.exists(), str(BUNDLED_FONT))
    if BUNDLED_FONT.exists():
        from PIL import ImageFont

        try:
            ImageFont.truetype(str(BUNDLED_FONT), 40)
            check("a fonte do projeto abre", True)
        except OSError as exc:
            check("a fonte do projeto abre", False, str(exc))

    _, unicode_ok = _load_font(40)
    check(
        "a capa usa uma fonte com acentos (não o fallback)",
        unicode_ok,
        "caiu no fallback sem acentos",
    )

    print("\n[fallback sem acentos]")
    amostra = "eleição política além do óbvio"
    check(
        "o fallback troca acentos por letras simples",
        _ascii_fold(amostra) == "eleicao politica alem do obvio",
        _ascii_fold(amostra),
    )

    print("\n[capa tipográfica com acentos]")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        cover = generate_title_cover(
            "Eleição, política e além: a ótica do país", "José Conceição", out
        )
        check("a capa foi gerada", cover.exists() and cover.stat().st_size > 0, str(cover))
        from PIL import Image

        with Image.open(cover) as image:
            check("dimensões esperadas", image.size == (800, 1200), str(image.size))

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
