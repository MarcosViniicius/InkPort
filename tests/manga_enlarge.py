"""Manga/comic text enlargement engine (no HTTP, no external tools).

Run with:  python tests/manga_enlarge.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORK = Path(tempfile.mkdtemp(prefix="opds_manga_"))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name if condition else f"{name}: {detail}")
    print(f"  {'ok  ' if condition else 'FAIL'} {name} {'' if condition else detail}")


def _font(size: int = 16):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow ignores the size argument
        return ImageFont.load_default()


def _edge_text_page():
    """Thin outline and text almost touching it (reproduces a detection bug)."""
    from PIL import Image, ImageDraw

    img = Image.new("L", (400, 400), 255)
    draw = ImageDraw.Draw(img)
    draw.ellipse((100, 100, 300, 300), outline=0, width=2)
    draw.text((103, 185), "Encostado", fill=0, font=_font(18))
    return img


def make_page() -> tuple:
    """White page, one round balloon with small text and a black artwork square."""
    from PIL import Image, ImageDraw

    img = Image.new("L", (480, 640), 255)
    draw = ImageDraw.Draw(img)
    draw.ellipse((60, 60, 360, 360), outline=0, width=3)
    draw.text((160, 190), "AAAA", fill=0, font=_font(16))
    draw.rectangle((40, 480, 220, 620), fill=0)  # artwork that must not change
    return img


def _dark_rows(img, box) -> int:
    """Rows containing dark pixels inside ``box`` (a rough text-height proxy)."""
    crop = img.crop(box).convert("L")
    dark = crop.point(lambda value: 255 if value < 100 else 0)
    width, height = dark.size
    data = dark.tobytes()
    rows = 0
    for y in range(height):
        row = data[y * width:(y + 1) * width]
        if any(row):
            rows += 1
    return rows


def main() -> int:
    from PIL import Image

    from app.converters.manga import enlarge_page
    from app.converters.manga.detector import Region, detect_regions
    from app.converters.manga.layout import plan_enlargement

    print("[layout: estratégia A e B]")
    big = Region((0, 0, 200, 200), (80, 80, 120, 100), "bubble", 40000)
    plan_a = plan_enlargement(big, max_scale=2.0, overflow=0.15, max_overflow_px=10)
    check("A: amplia com margem disponível", plan_a is not None and plan_a.strategy == "A")
    check("A: respeita o teto de escala", plan_a is not None and plan_a.scale <= 2.0 + 1e-6)

    tight = Region((0, 0, 120, 120), (10, 40, 110, 80), "bubble", 14400)
    plan_b = plan_enlargement(tight, max_scale=2.0, overflow=0.15, max_overflow_px=10)
    check("B: pouca margem ainda amplia", plan_b is not None and plan_b.strategy == "B")
    check("B: ampliação pequena e segura", plan_b is not None and 1.05 < plan_b.scale <= 1.3,
          str(plan_b.scale if plan_b else None))

    print("\n[detecção + ampliação em página sintética]")
    page = make_page()
    before_rows = _dark_rows(page, (100, 100, 320, 320))
    art_before = page.crop((40, 480, 220, 620)).tobytes()

    enlarged, stats = enlarge_page(page, options={"manga_max_scale": 2.0})
    check("detectou a região com texto", stats["regions"] >= 1, str(stats))
    check("ampliou ao menos uma região", stats["enlarged"] >= 1, str(stats))

    after_rows = _dark_rows(enlarged, (100, 100, 320, 320))
    check("o texto ficou maior (mais linhas de traço)",
          after_rows > before_rows, f"{before_rows} -> {after_rows}")
    check("a arte fora do balão ficou intacta",
          enlarged.crop((40, 480, 220, 620)).tobytes() == art_before)

    print("\n[robustez: páginas sem texto]")
    blank, blank_stats = enlarge_page(Image.new("L", (200, 200), 255))
    check("página em branco não gera regiões", blank_stats["enlarged"] == 0, str(blank_stats))
    check("página em branco continua válida", blank.size == (200, 200) and blank.mode == "L")

    print("\n[regressão: texto encostado no contorno e regiões duplicadas]")
    edge_page = _edge_text_page()
    regions = detect_regions(edge_page)
    check(
        "balão com texto encostado no contorno é detectado",
        len(regions) >= 1,
        str(len(regions)),
    )
    if regions:
        _, edge_stats = enlarge_page(edge_page.copy(), options={})
        check("e chega a ser ampliado", edge_stats["enlarged"] >= 1, str(edge_stats))

    from app.converters.manga.detector import _merge_overlapping

    duplicates = [(1000, (0, 0, 100, 100), "bubble"), (800, (30, 30, 130, 130), "bubble")]
    check("regiões sobrepostas viram uma só", len(_merge_overlapping(duplicates)) == 1)
    apart = [(1000, (0, 0, 100, 100), "bubble"), (900, (200, 0, 300, 100), "bubble")]
    check("regiões separadas continuam separadas", len(_merge_overlapping(apart)) == 2)
    touching = [(1000, (0, 0, 100, 100), "bubble"), (900, (90, 0, 190, 100), "bubble")]
    check("regiões que só se tocam não são fundidas", len(_merge_overlapping(touching)) == 2)

    print("\n[integração: conversão de CBZ com a ampliação ligada]")
    import zipfile

    from app.converters.runner import run_conversion
    from app.devices.registry import get_profile
    from app.library.detect import detect
    from app.metadata.extractor import extract_metadata
    from app.storage.temp import temp_workdir

    page_path = WORK / "page.png"
    page.save(page_path)
    cbz = WORK / "manga.cbz"
    with zipfile.ZipFile(cbz, "w") as archive:
        archive.write(page_path, "001.png")

    detection = detect(cbz)
    metadata = extract_metadata(cbz, detection)
    profile = get_profile("xteink_x4_pro")
    with temp_workdir("manga_test_") as workdir:
        result, _plan = run_conversion(
            source=cbz,
            detection=detection,
            metadata=metadata,
            profile=profile,
            workdir=workdir,
            target_format="epub",
            options={"manga_enlarge_text": True},
        )
        produced = result.output_path
        check("a conversão gera um arquivo", produced.exists() and produced.stat().st_size > 0)
        with zipfile.ZipFile(produced) as archive:
            check("EPUB válido com a ampliação ligada",
                  archive.read("mimetype").strip() == b"application/epub+zip")

    print("\n[desligado por padrão]")
    from app.converters.normalise import resolve_options

    options = resolve_options(
        __import__("app.devices.registry", fromlist=["get_profile"]).get_profile("eink_generic")
    )
    check("a opção começa desligada", options["manga_enlarge_text"] is False)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
