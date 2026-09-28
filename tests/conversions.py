"""Direct converter tests (no HTTP): PDF, EPUB, Calibre and image pipelines.

Run with:  python tests/conversions.py
"""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORK = Path(tempfile.mkdtemp(prefix="opds_conv_"))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name if condition else f"{name}: {detail}")
    print(f"  {'ok  ' if condition else 'FAIL'} {name} {'' if condition else detail}")


# --- fixtures -------------------------------------------------------------
def make_images(count: int = 4, size=(600, 900)) -> list[Path]:
    from PIL import Image

    out = []
    for index in range(count):
        img = Image.new("RGB", size, (240, 240 - index * 20, 200))
        path = WORK / f"src_{index:02d}.png"
        img.save(path)
        out.append(path)
    return out


def _image_sizes(epub_path: Path) -> list[tuple[int, int]]:
    from io import BytesIO

    from PIL import Image

    sizes: list[tuple[int, int]] = []
    with zipfile.ZipFile(epub_path) as zf:
        for name in zf.namelist():
            base = name.rsplit("/", 1)[-1]
            if name.startswith("OEBPS/images/") and base.startswith("page_") and base.lower().endswith((".jpg", ".png")):
                sizes.append(Image.open(BytesIO(zf.read(name))).size)
    return sizes


def make_cbz(images: list[Path]) -> Path:
    path = WORK / f"test_{len(list(WORK.glob('*.cbz')))}.cbz"
    with zipfile.ZipFile(path, "w") as zf:
        for index, image in enumerate(images, 1):
            zf.write(image, f"{index:03d}{image.suffix}")
    return path


def _make_large_images(count: int = 2, size=(3000, 4500)) -> list[Path]:
    from PIL import Image

    out = []
    for index in range(count):
        img = Image.new("L", size, 230 - index * 20)
        path = WORK / f"large_{index:02d}.png"
        img.save(path)
        out.append(path)
    return out


def make_text_pdf(pages: int = 4) -> Path:
    import pymupdf

    path = WORK / "text.pdf"
    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page()
        page.insert_text(
            (72, 100),
            f"Capítulo {index + 1}\n\n" + ("Texto de exemplo para reflow. " * 40),
            fontsize=12,
        )
    doc.save(str(path))
    doc.close()
    return path


def make_image_pdf(images: list[Path]) -> Path:
    import pymupdf

    path = WORK / "scan.pdf"
    doc = pymupdf.open()
    for image in images:
        page = doc.new_page(width=600, height=900)
        page.insert_image(pymupdf.Rect(0, 0, 600, 900), filename=str(image))
    doc.save(str(path))
    doc.close()
    return path


def _make_embedded_images(count: int = 3, size=(1200, 1800)) -> list[Path]:
    """Source images larger than the page box, so extraction is provable."""
    from PIL import Image

    out = []
    for index in range(count):
        img = Image.new("L", size, 235 - index * 25)
        path = WORK / f"scan_src_{index:02d}.png"
        img.save(path)
        out.append(path)
    return out


def make_epub(images: list[Path]) -> Path:
    from app.converters.epub import EpubMeta, write_epub

    path = WORK / "source.epub"
    write_epub(
        path,
        images=images,
        meta=EpubMeta(title="Livro de teste", author="Autor", language="pt"),
    )
    return path


# --- helpers --------------------------------------------------------------
def convert(source: Path, target: str | None, profile_slug: str = "generic_epub", options: dict | None = None):
    from app.converters.runner import run_conversion
    from app.devices.registry import get_profile
    from app.library.detect import detect
    from app.metadata.extractor import extract_metadata
    from app.storage.temp import temp_workdir

    detection = detect(source)
    metadata = extract_metadata(source, detection)
    profile = get_profile(profile_slug)
    with temp_workdir("convtest_") as workdir:
        result, plan = run_conversion(
            source=source,
            detection=detection,
            metadata=metadata,
            profile=profile,
            workdir=workdir,
            target_format=target,
            options=options or {},
        )
        target_path = WORK / f"out_{result.converter}_{plan.target_format}{result.output_path.suffix}"
        target_path.write_bytes(result.output_path.read_bytes())
        return result, plan, target_path


def valid_epub(path: Path) -> bool:
    """A valid EPUB: stored mimetype first plus a package document somewhere."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if "mimetype" not in names:
                return False
            if zf.read("mimetype").strip() != b"application/epub+zip":
                return False
            return any(name.lower().endswith(".opf") for name in names)
    except (zipfile.BadZipFile, KeyError):
        return False


def page_count_in_cbz(path: Path) -> int:
    with zipfile.ZipFile(path) as zf:
        return len([n for n in zf.namelist() if n.lower().endswith((".jpg", ".jpeg", ".png"))])


def main() -> int:
    from app.converters.tools import detect_toolchain

    chain = detect_toolchain()
    print(f"ferramentas: {chain.as_dict()}\n")

    images = make_images()
    cbz = make_cbz(images)
    epub = make_epub(images)
    text_pdf = make_text_pdf()
    scan_pdf = make_image_pdf(images)

    print("[CBZ -> EPUB para Xteink]")
    result, plan, out = convert(cbz, None, "xteink_x4_pro")
    check("gerado EPUB", out.suffix == ".epub" and valid_epub(out))
    check("usa pipeline de imagens", result.converter == "images_to_epub", result.converter)
    _check_device_size(out, 480, 800)
    check(
        "Xteink: páginas em PNG (16 níveis, sem artefato e menor que JPEG)",
        page_formats(out) == {"png"},
        str(page_formats(out)),
    )
    check(
        "Xteink: no máximo 16 níveis de cinza (a capacidade do painel)",
        _level_count(out) <= 16,
        str(_level_count(out)),
    )

    print("\n[imagens -> CBZ com ComicInfo]")
    result, plan, out = convert(epub, "cbz")
    check("CBZ gerado", out.suffix == ".cbz" and page_count_in_cbz(out) == len(images))
    with zipfile.ZipFile(out) as zf:
        check("ComicInfo incluído", "ComicInfo.xml" in zf.namelist())

    print("\n[EPUB -> EPUB otimizado para Kindle]")
    result, plan, out = convert(epub, "epub", "kindle_paperwhite")
    check("otimizador escolhido", result.converter == "epub_optimize", result.converter)
    check("EPUB válido após otimizar", valid_epub(out))

    print("\n[CBZ grande -> EPUB Universal (adaptável)]")
    large_cbz = make_cbz(_make_large_images())
    result, plan, out = convert(large_cbz, "epub", "generic_epub")
    check("EPUB universal gerado", out.suffix == ".epub" and valid_epub(out))
    sizes = _image_sizes(out)
    check(
        "universal mantém resolução e aplica só o teto (3000 -> 2000)",
        sizes and max(max(w, h) for w, h in sizes) == 2000,
        str(sizes),
    )
    check(
        "universal respeita o limite seguro 2048x3072",
        all(w <= 2048 and h <= 3072 for w, h in sizes),
        str(sizes),
    )

    print("\n[HTML (artigo de blog) -> EPUB nativo]")
    _html_article_test()
    _webpage_pipeline_test()
    _spreads_test()

    print("\n[PDF com texto -> EPUB]")
    result, plan, out = convert(text_pdf, None, "generic_epub")
    check("PDF convertido", out.suffix == ".epub" and valid_epub(out), str(out))
    print(f"       conversor: {result.converter} ({plan.reason})")

    print("\n[PDF digitalizado -> EPUB de imagens]")
    result, plan, out = convert(scan_pdf, "epub", "xteink_x4_pro")
    check("scan convertido", out.suffix == ".epub" and valid_epub(out))
    check("pipeline de imagens", result.converter == "images_to_epub", result.converter)

    print("\n[PDF scan com imagem embutida -> extração sem re-render]")
    scan_src = _make_embedded_images()
    embedded_pdf = make_image_pdf(scan_src)
    result, plan, out = convert(embedded_pdf, "epub", "generic_epub")
    sizes = _image_sizes(out)
    check(
        "imagem embutida extraída na resolução original (1200x1800)",
        sizes == [(1200, 1800)] * len(scan_src),
        str(sizes),
    )

    print("\n[PDF -> CBZ]")
    result, plan, out = convert(text_pdf, "cbz", "kobo_clara")
    check("PDF -> CBZ", out.suffix == ".cbz" and page_count_in_cbz(out) >= 4, str(out))

    print("\n[EPUB -> MOBI/AZW3 nativos, sem Calibre]")
    result, plan, out = convert(epub, "mobi", "kindle_legacy")
    check("MOBI gerado", out.exists() and out.stat().st_size > 0, str(out))
    check(
        "MOBI feito pelo escritor nativo (não pelo Calibre)",
        result.converter == "epub_to_kindle",
        result.converter,
    )
    result, plan, out = convert(epub, "azw3", "kindle_paperwhite")
    check("AZW3 gerado", out.exists() and out.stat().st_size > 0, str(out))
    check(
        "AZW3 feito pelo escritor nativo",
        result.converter == "epub_to_kindle",
        result.converter,
    )

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


def _level_count(epub_path: Path) -> int:
    """Maximum number of distinct gray levels among the page images."""
    import zipfile
    from io import BytesIO

    from PIL import Image

    highest = 0
    with zipfile.ZipFile(epub_path) as zf:
        for name in zf.namelist():
            base = name.rsplit("/", 1)[-1]
            if name.startswith("OEBPS/images/") and base.startswith("page_"):
                img = Image.open(BytesIO(zf.read(name))).convert("L")
                highest = max(highest, len(img.getcolors(maxcolors=256) or []))
    return highest


def page_formats(epub_path: Path) -> set[str]:
    import zipfile

    with zipfile.ZipFile(epub_path) as zf:
        return {
            n.rsplit(".", 1)[-1].lower()
            for n in zf.namelist()
            if n.startswith("OEBPS/images/") and n.rsplit("/", 1)[-1].startswith("page_")
        }


def _html_article_test() -> None:
    """The built-in HTML -> EPUB path (used for RSS articles), without Calibre."""
    from app.converters.base import ConversionRequest
    from app.converters.strategies.html_to_epub import HtmlToEpubConverter
    from app.devices.registry import get_profile
    from app.library.detect import detect
    from app.metadata.model import BookMetadata

    html = WORK / "artigo.html"
    html.write_text(
        "<!doctype html><html lang='pt-br'><head><title>Meu Artigo</title>"
        "<meta name='author' content='Fulano'></head><body>"
        "<nav>menu que deve ser ignorado</nav>"
        "<h1>Introdução</h1><p>Primeiro paragrafo do artigo.</p>"
        "<p>Segundo paragrafo.</p><script>var x=1;</script>"
        "<h2>Conclusão</h2><p>Fim.</p></body></html>",
        encoding="utf-8",
    )
    detection = detect(html)
    check("HTML detectado como e-book", detection.content_type == "ebook", detection.content_type)
    check("HTML tem MIME text/html", detection.media_type == "text/html", detection.media_type)

    request = ConversionRequest(
        source=html,
        detection=detection,
        metadata=BookMetadata(title="Meu Artigo"),
        profile=get_profile("generic_epub"),
        target_format="epub",
        workdir=WORK,
        options={},
    )
    result = HtmlToEpubConverter().run(request)
    check("EPUB de artigo válido", valid_epub(result.output_path))

    with zipfile.ZipFile(result.output_path) as zf:
        text = "".join(
            zf.read(n).decode("utf-8", "replace")
            for n in zf.namelist() if n.endswith(".xhtml")
        )
    check("texto do artigo presente", "Primeiro paragrafo do artigo." in text)
    check("menu de navegação ignorado", "menu que deve ser ignorado" not in text)
    check("script ignorado", "var x=1" not in text)
    # Regression: HTML without a <meta charset> was parsed as Latin-1 by libxml2,
    # which turned "Introdução" into "IntroduÃ§Ã£o" inside the EPUB.
    check(
        "acentos preservados (UTF-8, sem mojibake)",
        "Introdução" in text and "Conclusão" in text and "Ã" not in text,
        repr([line for line in text.splitlines() if "Introdu" in line][:1]),
    )


def _webpage_pipeline_test() -> None:
    """Same pipeline as webpagetoepub.github.io: images, links, chapters."""
    from io import BytesIO

    from PIL import Image

    from app.converters.epub import EpubMeta
    from app.converters.epub.text_builder import write_text_epub
    from app.converters.webpage import convert_webpage

    buffer = BytesIO()
    Image.new("RGB", (40, 30), (10, 120, 200)).save(buffer, "PNG")
    png = buffer.getvalue()

    requested: list[str] = []

    def fetcher(url: str):
        requested.append(url)
        if "quebrada" in url:
            return None
        return (png, "image/png")

    html = (
        "<!doctype html><html lang='pt'><head><title>Pagina</title>"
        "<meta name='author' content='Fulano'>"
        "<meta name='keywords' content='um, dois'></head><body>"
        "<header>cabeçalho do site</header>"
        "<main><article>"
        "<h1>Titulo</h1>"
        "<p>Texto <a href='/outra'>link relativo</a> e <a href='#topo'>ancora local</a>.</p>"
        "<img src='foto.png' alt='Foto'>"
        "<img data-src='lazy.png' alt='Lazy'>"
        "<img src='quebrada.png' alt='Nao carrega'>"
        "<h2>Secao 1</h2><p>Conteudo da secao um.</p>"
        "<h2>Secao 2</h2><p>Conteudo da secao dois com <mark>marca</mark>.</p>"
        "<script>alert(1)</script>"
        "</article></main>"
        "<footer>rodapé do site</footer></body></html>"
    )
    page = convert_webpage(html, "https://exemplo.com/post/1", fetcher)

    check("pipeline: título da página", page.title == "Pagina", page.title)
    check("pipeline: autor do metatag", page.author == "Fulano", page.author)
    check("pipeline: idioma", page.language == "pt", page.language)
    check("pipeline: tags separadas por vírgula", page.tags == ["um", "dois"], str(page.tags))
    check("pipeline: capítulos por h2", len(page.chapters) >= 1, str(len(page.chapters)))

    bodies = " ".join(body for _t, body in page.chapters)
    check("pipeline: imagem relativa resolvida contra a página",
          "https://exemplo.com/post/foto.png" in requested, str(requested))
    check("pipeline: imagem 'lazy' (data-src) recuperada",
          "https://exemplo.com/post/lazy.png" in requested, str(requested))
    check("pipeline: imagem embutida (2 boas, 1 quebrada descartada)",
          len(page.images) == 2, str(len(page.images)))
    check("pipeline: referência da imagem reescrita", "images/" in bodies)
    check("pipeline: link relativo virou absoluto", "https://exemplo.com/outra" in bodies)
    check("pipeline: conteúdo principal escolhido (main/article)",
          "cabeçalho do site" not in bodies and "rodapé do site" not in bodies)
    check("pipeline: <script> removido", "alert(1)" not in bodies)
    check("pipeline: atributo class/style removidos", "class=" not in bodies)
    check("pipeline: artigo curto vira UM capítulo (sem páginas extras)",
          len(page.chapters) == 1, str(len(page.chapters)))
    check("pipeline: as seções continuam no texto",
          "Conteudo da secao um." in bodies and "Conteudo da secao dois" in bodies)

    _chapter_consolidation_test()

    out = WORK / "webpage.epub"
    write_text_epub(
        out,
        meta=EpubMeta(title=page.title, author=page.author, language=page.language),
        chapters=page.chapters,
        images=[(image.name, image.data, image.mime) for image in page.images],
    )
    check("pipeline: EPUB válido", valid_epub(out))
    with zipfile.ZipFile(out) as zf:
        check(
            "pipeline: imagem presente no EPUB",
            any(n.startswith("OEBPS/images/") for n in zf.namelist()),
            str(zf.namelist()[-3:]),
        )


def _chapter_consolidation_test() -> None:
    """Fewer chapters: short sections are merged, short articles stay whole."""
    from app.converters.webpage.split import consolidate_chapters

    short = [("A", "<p>" + "x" * 100 + "</p>"), ("B", "<p>" + "y" * 100 + "</p>")]
    check("consolidação: artigo curto vira 1 capítulo",
          len(consolidate_chapters(short)) == 1, str(len(consolidate_chapters(short))))

    long_sections = [
        ("Intro", "<p>" + "a" * 8000 + "</p>"),
        ("Mini", "<p>curto</p>"),
        ("Grande", "<p>" + "b" * 30000 + "</p>"),
    ]
    result = consolidate_chapters(long_sections)
    check("consolidação: seção minúscula é absorvida",
          len(result) == 2 and result[0][0] == "Intro", str([t for t, _ in result]))
    check("consolidação: conteúdo da seção absorvida é preservado",
          "curto" in result[0][1])

    many = [("S1", "<p>" + "z" * 6000 + "</p>")] + [
        (f"S{i}", "<p>" + "w" * 3000 + "</p>") for i in range(2, 20)
    ]
    result = consolidate_chapters(many)
    check("consolidação: artigo longo vira poucos capítulos",
          2 <= len(result) <= 8, str([t for t, _ in result]))


def _spreads_test() -> None:
    """Wide pages follow KCC: split in two, or rotated when very wide."""
    from io import BytesIO

    from PIL import Image

    print("\n[Spreads de mangá (lógica do KCC)]")

    def page(size, fill):
        img = Image.new("L", size, fill)
        buffer = BytesIO()
        img.save(buffer, "PNG")
        return buffer.getvalue()

    # left half white, right half black -> tells the halves apart
    spread = Image.new("L", (1200, 800), 255)
    spread.paste(Image.new("L", (600, 800), 0), (600, 0))
    buffer = BytesIO()
    spread.save(buffer, "PNG")

    cbz = WORK / "spreads.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        zf.writestr("001.png", page((600, 900), 200))            # normal (portrait)
        zf.writestr("002.png", buffer.getvalue())                # spread 1.5 -> divide
        zf.writestr("003.png", page((1800, 600), 120))           # muito larga 3.0 -> gira

    result, _plan, out = convert(
        cbz, "epub", "xteink_x4_pro", options={"reading_direction": "ltr"}
    )
    sizes = _image_sizes(out)
    check("spread 1.5x dividido em duas páginas (3 -> 4 páginas)",
          len(sizes) == 4, str(sizes))
    check("página muito larga foi girada (retrato, não tira)",
          sizes and sizes[-1][1] > sizes[-1][0], str(sizes[-1] if sizes else None))

    meios = _page_means(out)
    check("LTR: primeira metade é a da esquerda (clara)", meios and meios[1] > 200, str(meios))
    check("LTR: segunda metade é a da direita (escura)", len(meios) > 2 and meios[2] < 60, str(meios))

    # Manga is read right to left: the right half must come first.
    result, _plan, out_rtl = convert(
        cbz, "epub", "xteink_x4_pro_manga", options={"reading_direction": "rtl"}
    )
    meios_rtl = _page_means(out_rtl)
    check("RTL (mangá): primeira metade é a da direita (escura)",
          meios_rtl and meios_rtl[1] < 60, str(meios_rtl))

    # With spreads disabled the page is kept whole.
    result, _plan, out_none = convert(cbz, "epub", "generic_epub")
    check("modo 'none' não mexe nas spreads",
          len(_image_sizes(out_none)) == 3, str(_image_sizes(out_none)))


def _page_means(epub_path: Path) -> list[float]:
    import zipfile
    from io import BytesIO
    from statistics import mean

    from PIL import Image

    means: list[float] = []
    with zipfile.ZipFile(epub_path) as zf:
        for name in sorted(n for n in zf.namelist() if n.startswith("OEBPS/images/page_")):
            img = Image.open(BytesIO(zf.read(name))).convert("L")
            means.append(round(mean(img.getdata()), 1))
    return means


def _check_device_size(epub_path: Path, width: int, height: int) -> None:
    """Confirm every image inside fits the device panel."""
    from io import BytesIO

    from PIL import Image

    ok = True
    with zipfile.ZipFile(epub_path) as zf:
        for name in zf.namelist():
            if name.startswith("OEBPS/images/") and name.lower().endswith((".jpg", ".png")):
                img = Image.open(BytesIO(zf.read(name)))
                fits = img.width <= width and img.height <= height
                if not fits:
                    ok = False
                grey = img.mode == "L"
                if not grey:
                    ok = False
    check(f"imagens em tons de cinza e até {width}x{height}", ok)


if __name__ == "__main__":
    raise SystemExit(main())
