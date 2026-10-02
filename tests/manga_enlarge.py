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


def _wide_line_page():
    """One long line that cannot grow uniformly but can be re-broken."""
    from PIL import Image, ImageDraw

    img = Image.new("L", (1000, 1000), 255)
    draw = ImageDraw.Draw(img)
    draw.ellipse((150, 150, 850, 850), outline=0, width=3)
    draw.text((250, 480), "PALAVRA PALAVRA PALAVRA PALAVRA", fill=0, font=_font(28))
    return img


def _rtl_page():
    from PIL import Image, ImageDraw

    img = Image.new("L", (600, 500), 255)
    draw = ImageDraw.Draw(img)
    draw.ellipse((60, 60, 540, 440), outline=0, width=3)
    draw.text((160, 230), "DIREITA ESQUERDA", fill=0, font=_font(24))
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

    enlarged, stats = enlarge_page(page, options={"manga_text_mode": "experimental"})
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
        _, edge_stats = enlarge_page(
            edge_page.copy(), options={"manga_text_mode": "experimental"}
        )
        check("e chega a ser ampliado", edge_stats["enlarged"] >= 1, str(edge_stats))

    from app.converters.manga.detector import _merge_overlapping

    duplicates = [(1000, (0, 0, 100, 100), "bubble"), (800, (30, 30, 130, 130), "bubble")]
    check("regiões sobrepostas viram uma só", len(_merge_overlapping(duplicates)) == 1)
    apart = [(1000, (0, 0, 100, 100), "bubble"), (900, (200, 0, 300, 100), "bubble")]
    check("regiões separadas continuam separadas", len(_merge_overlapping(apart)) == 2)
    touching = [(1000, (0, 0, 100, 100), "bubble"), (900, (90, 0, 190, 100), "bubble")]
    check("regiões que só se tocam não são fundidas", len(_merge_overlapping(touching)) == 2)

    print("\n[reflow: quebra de linha para ampliar]")
    from app.converters.manga.layout import usable_box
    from app.converters.manga.reflow import plan_reflow
    from app.converters.manga.segmentation import segment_words

    wide = _wide_line_page()
    wide_regions = detect_regions(wide)
    check("página de linha larga detectada", len(wide_regions) == 1, str(len(wide_regions)))
    if wide_regions:
        region = wide_regions[0]
        fit = usable_box(wide, region.box, region.text_box)
        block = segment_words(wide, region.box, right_to_left=False)
        reflow = plan_reflow(block, region.box, max_scale=2.5, fit_box=fit) if block else None
        uniform = plan_enlargement(region, max_scale=2.5, fit_box=fit)
        check(
            "reflow amplia onde a escala uniforme não consegue",
            reflow is not None and (uniform is None or reflow.scale > uniform.scale),
            f"reflow={reflow.scale if reflow else None} uniform={uniform.scale if uniform else None}",
        )
        if reflow and block:
            check(
                "reflow usa todas as palavras",
                sum(len(line) for line in reflow.lines) == len(block.words),
            )
            inside = all(
                region.box[0] <= item.target[0] and item.target[2] <= region.box[2]
                and region.box[1] <= item.target[1] and item.target[3] <= region.box[3]
                for line in reflow.lines
                for item in line
            )
            check("texto reposicionado dentro do balão", inside)

    print("\n[reflow: ordem de leitura RTL (mangá)]")
    rtl_page = _rtl_page()
    rtl_regions = detect_regions(rtl_page)
    check("página RTL detectada", len(rtl_regions) >= 1, str(len(rtl_regions)))
    if rtl_regions:
        region = rtl_regions[0]
        block = segment_words(rtl_page, region.box, right_to_left=True)
        if block and len(block.lines[0]) >= 2:
            first = block.words[block.lines[0][0]]
            second = block.words[block.lines[0][1]]
            check("RTL: a primeira palavra é a da direita", first.box[0] > second.box[0])
            fit = usable_box(rtl_page, region.box, region.text_box)
            plan = plan_reflow(block, region.box, max_scale=2.0, fit_box=fit)
            if plan:
                targets = [item.target for line in plan.lines for item in line]
                check(
                    "RTL: destino preserva direita->esquerda, cima->baixo",
                    targets[0][1] < targets[1][1]
                    or (targets[0][1] == targets[1][1] and targets[0][0] > targets[1][0]),
                    str(targets[:2]),
                )
        else:
            check("RTL: palavras segmentadas", False, "menos de duas palavras na linha")

    print("\n[OCR: palavras reconhecidas -> reescrita e reflow]")
    from app.converters.manga import fonts
    from app.converters.manga.ocr import OcrLine, OcrWord
    from app.converters.manga.relayout import block_from_ocr, plan_rewrite, render_rewrite

    check("fonte embutida presente no projeto", fonts.BUNDLED_FONT.exists())

    ocr_page = _wide_line_page()
    ocr_regions = detect_regions(ocr_page)
    if ocr_regions:
        region = ocr_regions[0]
        words = [
            OcrWord(f"PALAVRA{index}", 0.95, (250 + index * 120, 480, 340 + index * 120, 512))
            for index in range(4)
        ]
        line = OcrLine(
            "PALAVRA PALAVRA PALAVRA PALAVRA", 0.95,
            (words[0].box[0], 480, words[-1].box[2], 512), words,
        )
        block = block_from_ocr([line])
        check("bloco montado a partir do OCR", block is not None and len(block.words) == 4)
        if block:
            lefts = [word.box[0] for word in block.words]
            check("ordem de leitura LTR preservada", lefts == sorted(lefts))
            fit = usable_box(ocr_page, region.box, region.text_box)
            plan = plan_reflow(block, region.box, max_scale=2.5, fit_box=fit)
            check(
                "reflow conservador amplia usando o OCR",
                plan is not None and plan.scale > 1.0,
                str(plan.scale if plan else None),
            )

        rewrite = plan_rewrite([line], region.box, fit_box=usable_box(
            ocr_page, region.box, region.text_box
        ))
        check(
            "planeja reescrita com fonte",
            rewrite is not None and rewrite.size >= 8,
            str(rewrite.size if rewrite else None),
        )
        if rewrite is not None:
            before = ocr_page.copy()
            render_rewrite(ocr_page, rewrite)
            check("a página foi reescrita", ocr_page.tobytes() != before.tobytes())

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
