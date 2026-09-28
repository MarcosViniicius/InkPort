"""Native PDF converters: text extraction and EPUB -> PDF, without Calibre.

Run with:  python tests/native_pdf.py

The real library PDF is used when present, with Calibre run *only as a reference*
to compare how much text each path recovers. Everything else is synthetic
fixtures generated with PyMuPDF.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORK = Path(tempfile.mkdtemp(prefix="opds_nativepdf_"))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _tokens(text: str) -> set[str]:
    """Case-folded word set, ignoring leading/trailing punctuation."""
    out = set()
    for word in text.split():
        cleaned = re.sub(r"^\W+|\W+$", "", word).casefold()
        if cleaned:
            out.add(cleaned)
    return out


def _visible_text_from_epub(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        parts = []
        for name in zf.namelist():
            if name.lower().endswith((".xhtml", ".html", ".htm")):
                raw = zf.read(name).decode("utf-8", "replace")
                body = re.search(r"<body[^>]*>(.*)</body>", raw, re.S | re.I)
                parts.append(re.sub(r"<[^>]+>", " ", body.group(1) if body else raw))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _pdf_plain_text(path: Path) -> str:
    import pymupdf

    with pymupdf.open(str(path)) as doc:
        return " ".join(doc[i].get_text("text") for i in range(doc.page_count))


def _coverage(pdf_text: str, output_text: str) -> float:
    pdf_tokens = _tokens(pdf_text)
    if not pdf_tokens:
        return 1.0
    return len(pdf_tokens & _tokens(output_text)) / len(pdf_tokens)


def _library_pdfs() -> list[Path]:
    # The library is organised in category folders, so look recursively.
    return sorted((ROOT / "data" / "library").rglob("*.pdf"))


def _library_epub_with_images() -> Path | None:
    for path in sorted((ROOT / "data" / "library").rglob("*.epub")):
        try:
            with zipfile.ZipFile(path) as zf:
                has_image = any(
                    n.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))
                    for n in zf.namelist()
                )
            if has_image:
                return path
        except zipfile.BadZipFile:
            continue
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def make_pdf(path: Path, draw) -> Path:
    import pymupdf

    doc = pymupdf.open()
    try:
        draw(doc)
        doc.save(str(path))
    finally:
        doc.close()
    return path


def make_two_column_pdf(path: Path) -> Path:
    def draw(doc):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 60), "Titulo do Artigo", fontsize=16)
        for i, line in enumerate(
            ["Coluna esquerda linha um do texto", "esquerda linha dois continuando aqui",
             "esquerda linha tres final do paragrafo."]
        ):
            page.insert_text((60, 110 + i * 14), line, fontsize=10)
        for i, line in enumerate(
            ["Coluna direita linha um do texto", "direita linha dois continuando aqui",
             "direita linha tres final do paragrafo."]
        ):
            page.insert_text((320, 110 + i * 14), line, fontsize=10)

    return make_pdf(path, draw)


def make_reflow_pdf(path: Path) -> Path:
    """Hyphenation, a paragraph continuing on the next page, header and folio."""

    def draw(doc):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 40), "LIVRO DE TESTE", fontsize=9)
        page.insert_text((300, 820), "1", fontsize=9)
        page.insert_text((72, 100), "Esta palavra fica inquie-", fontsize=11)
        page.insert_text((72, 114), "tação no leitor. E a frase", fontsize=11)
        next_page = doc.new_page(width=595, height=842)
        next_page.insert_text((72, 40), "LIVRO DE TESTE", fontsize=9)
        next_page.insert_text((300, 820), "2", fontsize=9)
        next_page.insert_text((72, 100), "comeca na pagina anterior", fontsize=11)
        next_page.insert_text((72, 114), "e ainda nao terminou", fontsize=11)

    return make_pdf(path, draw)


def make_figure_pdf(path: Path) -> Path:
    import pymupdf
    from PIL import Image

    color = Image.new("RGB", (400, 300), (200, 40, 40))
    color.save(WORK / "figure_source.png")

    def draw(doc):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), "Texto antes da figura do capitulo.", fontsize=11)
        page.insert_image(
            pymupdf.Rect(100, 150, 400, 375), filename=str(WORK / "figure_source.png")
        )
        page.insert_text((72, 420), "Texto depois da figura do capitulo.", fontsize=11)

    return make_pdf(path, draw)


def make_scanned_figure_pdf(path: Path) -> Path:
    """A page that is one full-page image with a text layer (print-to-PDF).

    The diagram has no separate image: its labels are small text drawn inside the
    figure and the caption sits below, exactly like a scanned paper.
    """
    import pymupdf
    from PIL import Image, ImageDraw

    canvas = Image.new("RGB", (1240, 1754), (255, 255, 255))
    drawer = ImageDraw.Draw(canvas)
    drawer.rectangle((220, 500, 1020, 900), fill=(230, 240, 255), outline=(20, 20, 20), width=3)
    drawer.rectangle((260, 560, 980, 640), fill=(255, 245, 200), outline=(20, 20, 20), width=2)
    drawer.rectangle((260, 700, 980, 780), fill=(255, 230, 230), outline=(20, 20, 20), width=2)
    canvas.save(WORK / "scanned_page.png")

    def draw(doc):
        page = doc.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(0, 0, 595, 842), filename=str(WORK / "scanned_page.png"))
        page.insert_text((60, 120), "Corpo do capitulo antes da figura, com texto normal.", fontsize=10)
        # Rótulos dentro do desenho: pequenos e curtos.
        page.insert_text((150, 300), "Soma e norma", fontsize=6)
        page.insert_text((150, 370), "Atenção", fontsize=6)
        page.insert_text((60, 430), "Figura 1: Diagrama do modelo usado no teste.", fontsize=10)
        page.insert_text((60, 470), "Corpo do capitulo depois da figura.", fontsize=10)

    return make_pdf(path, draw)


def figures_test() -> None:
    """Diagramas de páginas digitalizadas viram figuras; o texto deles sai."""
    from app.converters.native.pdf_text import extract_pdf_text

    print("\n[página digitalizada com diagrama -> figura preservada]")
    pdf = make_scanned_figure_pdf(WORK / "scanned.pdf")
    extracted = extract_pdf_text(pdf)
    texto = " ".join(xhtml for _title, xhtml in extracted.chapters)
    check("extraiu a figura do diagrama", len(extracted.images) >= 1, str(len(extracted.images)))
    check("figura referenciada no texto", "<figure>" in texto and "<img" in texto)
    check(
        "rótulos do diagrama não viraram parágrafos",
        "Soma e norma" not in texto and "Atenção" not in texto,
        texto[:120],
    )
    check("o corpo do texto continua lá", "Corpo do capitulo antes" in texto)
    check("a legenda permanece", "Figura 1:" in texto)
    if extracted.images:
        name, data, mime = extracted.images[0]
        check("figura é uma imagem válida", len(data) > 2000 and mime.startswith("image/"), f"{name} {mime}")

    figures = _library_pdfs()
    if figures:
        real = extract_pdf_text(figures[0])
        check(
            "PDF do acervo também rende figuras",
            len(real.images) >= 1,
            f"{len(real.images)} imagem(ns)",
        )


def make_table_pdf(path: Path) -> Path:
    """A small bordered table with a caption, like a paper's Table 1."""
    def draw(doc):
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 80), "Texto antes da tabela.", fontsize=10)
        linhas = [(120, 200, 200), (150, 260, 320), (180, 320, 420)]
        colunas = [(120, 240), (240, 360), (360, 480)]
        for x0, y0, _y1 in linhas:
            page.draw_line((x0, y0), (480, y0), width=0.8)
        page.draw_line((120, 320), (480, 320), width=0.8)
        for x0, _x1 in colunas:
            page.draw_line((x0, 120), (x0, 320), width=0.8)
        page.insert_text((130, 170), "Tipo", fontsize=9)
        page.insert_text((250, 170), "Custo", fontsize=9)
        page.insert_text((370, 170), "Tempo", fontsize=9)
        page.insert_text((130, 230), "Leve", fontsize=9)
        page.insert_text((250, 230), "10", fontsize=9)
        page.insert_text((370, 230), "1s", fontsize=9)
        page.insert_text((130, 290), "Pesado", fontsize=9)
        page.insert_text((250, 290), "99", fontsize=9)
        page.insert_text((370, 290), "9s", fontsize=9)
        page.insert_text((60, 350), "Tabela 1: Comparação usada no teste.", fontsize=10)
        page.insert_text((60, 390), "Texto depois da tabela.", fontsize=10)

    return make_pdf(path, draw)


def table_test() -> None:
    """Uma tabela com moldura e legenda vira <table> de verdade."""
    from app.converters.native.pdf_text import extract_pdf_text

    print("\n[tabela com legenda -> <table> pesquisável]")
    pdf = make_table_pdf(WORK / "tabela.pdf")
    extracted = extract_pdf_text(pdf)
    texto = " ".join(xhtml for _title, xhtml in extracted.chapters)
    tables = re.findall(r"<table>.*?</table>", texto, re.DOTALL)
    check("reconstruiu a tabela como HTML", len(tables) == 1, str(len(tables)))
    if tables:
        celulas = re.findall(r"<t[hd]>(.*?)</t[hd]>", tables[0], re.DOTALL)
        for esperado in ("Tipo", "Custo", "Tempo", "Leve", "Pesado", "99"):
            check(f"célula {esperado!r} presente", esperado in celulas, str(celulas))
        check("a legenda continua como texto", "Tabela 1:" in texto)
        check("o texto em volta continua", "Texto antes" in texto and "Texto depois" in texto)
    check(
        "a tabela não virou imagem",
        not any("figura_" in name for name, _d, _m in extracted.images),
        str([name for name, _d, _m in extracted.images]),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def real_pdf_test() -> None:
    from app.converters.epub.model import EpubMeta
    from app.converters.epub.text_builder import write_text_epub
    from app.converters.native.pdf_text import extract_pdf_text

    print("\n[PDF real do acervo -> XHTML refluido]")
    pdfs = _library_pdfs()
    if not pdfs:
        print("  (sem PDF em data/library -- teste pulado)")
        return

    pdf = pdfs[0]
    result = extract_pdf_text(pdf)
    check("páginas lidas", result.page_count >= 1, str(result.page_count))
    check("título preenchido", bool(result.title), result.title)

    epub = WORK / "real.epub"
    write_text_epub(
        epub,
        meta=EpubMeta(title=result.title, author=result.author, language=result.language),
        chapters=result.chapters,
        images=result.images,
    )
    check("EPUB gerado a partir do PDF", epub.exists() and epub.stat().st_size > 0)

    our_text = _visible_text_from_epub(epub)
    pdf_text = _pdf_plain_text(pdf)
    coverage = _coverage(pdf_text, our_text)
    # 93%: tabelas e figuras viram imagem (fidelidade visual), então as palavras
    # *dentro* delas deixam de aparecer como texto de propósito.
    check(
        ">= 93% das palavras do PDF aparecem na saída",
        coverage >= 0.93,
        f"{coverage * 100:.2f}%",
    )
    check(
        "estrutura de capítulos detectada (mais de um capítulo)",
        len(result.chapters) >= 2,
        str([title for title, _ in result.chapters]),
    )

    # Whole sentences survive the reflow (sampled from the PDF itself).
    flat = re.sub(r"\s+", " ", pdf_text)
    sentences = [
        s for s in re.split(r"(?<=[.!?])\s+", flat)
        if 60 <= len(s) <= 200 and "-" not in s and not any(c.isdigit() for c in s)
    ][:8]
    found = sum(1 for s in sentences if s in our_text)
    check(
        "frases/parágrafos inteiros preservados",
        bool(sentences) and found >= max(1, len(sentences) // 2),
        f"{found}/{len(sentences)}",
    )

    _calibre_comparison(pdf, our_text, coverage)


def _calibre_comparison(pdf: Path, our_text: str, our_coverage: float) -> None:
    import shutil

    from app.converters.capabilities import detect_toolchain

    # Calibre is optional now: it is only used here as an external reference.
    exe = detect_toolchain().optional.get("Calibre") or shutil.which("ebook-convert")
    if not exe:
        print("  (Calibre não encontrado -- comparação pulada)")
        return

    out = WORK / "calibre.epub"
    try:
        subprocess.run(
            [exe, str(pdf), str(out), "--output-profile=default"],
            capture_output=True, timeout=900, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  (Calibre falhou -- comparação pulada: {exc})")
        return
    if not out.exists():
        print("  (Calibre não produziu EPUB -- comparação pulada)")
        return

    calibre_text = _visible_text_from_epub(out)
    calibre_coverage = _coverage(_pdf_plain_text(pdf), calibre_text)
    print(
        f"       Calibre: {len(calibre_text.split())} palavras, "
        f"{calibre_coverage * 100:.2f}% de cobertura"
    )
    print(
        f"       nativo:  {len(our_text.split())} palavras, "
        f"{our_coverage * 100:.2f}% de cobertura"
    )
    check(
        "cobertura nativa >= cobertura do Calibre",
        our_coverage >= calibre_coverage - 0.005,
        f"nativo {our_coverage * 100:.2f}% vs calibre {calibre_coverage * 100:.2f}%",
    )


def two_column_test() -> None:
    from app.converters.native.pdf_text import extract_pdf_text

    print("\n[PDF de duas colunas -- ordem de leitura]")
    pdf = make_two_column_pdf(WORK / "two_columns.pdf")
    result = extract_pdf_text(pdf)
    text = " ".join(body for _title, body in result.chapters)
    check(
        "coluna esquerda lida antes da direita",
        text.find("esquerda linha um") != -1
        and text.find("esquerda linha um") < text.find("direita linha um"),
        text[:160],
    )
    left = " ".join(["Coluna esquerda linha um do texto",
                     "esquerda linha dois continuando aqui",
                     "esquerda linha tres final do paragrafo."])
    right = " ".join(["Coluna direita linha um do texto",
                      "direita linha dois continuando aqui",
                      "direita linha tres final do paragrafo."])
    check("parágrafos das colunas não se misturam", left in text and right in text)


def reflow_test() -> None:
    from app.converters.native.pdf_text import extract_pdf_text

    print("\n[PDF com hifenização, cabeçalho e continuação de página]")
    pdf = make_reflow_pdf(WORK / "reflow.pdf")
    result = extract_pdf_text(pdf)
    text = " ".join(body for _title, body in result.chapters)
    check("palavra hifenizada na quebra recomposta", "inquietação" in text, text[:160])
    check("cabeçalho repetido removido", "LIVRO DE TESTE" not in text)
    check("número de página removido", not re.search(r"<p>1</p>|<p>2</p>", text))
    check(
        "parágrafo que continua na página seguinte é unido",
        "a frase comeca na pagina anterior e ainda nao terminou" in text,
        text,
    )


def figure_test() -> None:
    from app.converters.native.pdf_text import extract_pdf_text

    print("\n[PDF com figura embutida -- imagem preservada]")
    pdf = make_figure_pdf(WORK / "figure.pdf")
    result = extract_pdf_text(pdf)
    bodies = " ".join(body for _title, body in result.chapters)
    check("imagem extraída", len(result.images) == 1, str(len(result.images)))
    check("imagem referenciada no XHTML", "images/" in bodies)
    check(
        "texto antes e depois da imagem preservado",
        "antes da figura" in bodies and "depois da figura" in bodies,
    )
    if result.images:
        name, data, mime = result.images[0]
        check("imagem com dados e MIME", bool(data) and mime.startswith("image/"), f"{name} {mime}")


def epub_to_pdf_test() -> None:
    import pymupdf

    from app.converters.epub.model import EpubMeta
    from app.converters.epub.text_builder import write_text_epub
    from app.converters.native.pdf_writer import epub_to_pdf

    print("\n[EPUB do acervo -> PDF]")
    source = _library_epub_with_images()
    if source is None:
        # Fallback: build a small EPUB with an image so the path is still covered.
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (300, 200), (30, 120, 200)).save(buffer, "PNG")
        source = WORK / "synthetic.epub"
        write_text_epub(
            source,
            meta=EpubMeta(title="Sintetico", author="Teste", language="pt"),
            chapters=[
                ("Capitulo 1", "<p>" + "conteudo do livro " * 300 + "</p>"),
                ("Capitulo 2", '<p>texto final</p><figure><img src="images/foto.png"/></figure>'),
            ],
            images=[("foto.png", buffer.getvalue(), "image/png")],
        )

    out = WORK / "from_epub.pdf"
    epub_to_pdf(
        source, out,
        page_width_pt=420, page_height_pt=600, margin_pt=20, title="Convertido",
    )
    check("PDF gerado", out.exists() and out.stat().st_size > 0, str(out))
    check(
        "tamanho controlado (imagens recompressas)",
        out.stat().st_size < 50_000_000,
        f"{out.stat().st_size / 1e6:.2f} MB",
    )

    with zipfile.ZipFile(source) as zf:
        source_has_images = any(
            n.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))
            for n in zf.namelist()
        )

    with pymupdf.open(str(out)) as doc:
        check("abre no PyMuPDF", doc.page_count >= 1)
        check("mais de uma página", doc.page_count > 1, str(doc.page_count))
        text = " ".join(doc[i].get_text() for i in range(doc.page_count))
        check("texto extraível", len(text.strip()) > 200, str(len(text.strip())))
        images = sum(len(doc[i].get_images()) for i in range(doc.page_count))
        if source_has_images:
            check("imagens presentes no PDF", images > 0, str(images))
        else:
            print(f"  (EPUB sem imagens; {images} imagens no PDF)")
    print(f"       {source.name}: PDF com {out.stat().st_size} bytes")


def main() -> int:
    real_pdf_test()
    two_column_test()
    reflow_test()
    figure_test()
    figures_test()
    table_test()
    epub_to_pdf_test()

    shutil.rmtree(WORK, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
