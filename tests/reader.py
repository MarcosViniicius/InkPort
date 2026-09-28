"""Web Reader tests (handlers, manifest, sanitisation, path containment, cache).

Run with:  python tests/reader.py
Boots the real app on a throwaway DATA_DIR and imports generated fixtures.
"""

from __future__ import annotations

import base64
import io
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="opds_reader_"))
os.environ["DATA_DIR"] = str(WORKDIR)
os.environ["SECRET_KEY"] = "reader-secret"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "admin"
os.environ["RSS_WORKER_ENABLED"] = "false"
os.environ["REQUIRE_AUTH_PANEL"] = "false"
os.environ["BASE_URL"] = "http://testserver"

PASSED: list[str] = []
FAILED: list[str] = []
FIXTURES = WORKDIR / "fixtures"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Livro de Teste</dc:title><dc:creator>Fulano</dc:creator><dc:language>pt</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="styles/style.css" media-type="text/css"/>
    <item id="img" href="images/pic.png" media-type="image/png"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""

NAV = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Índice</title></head><body>
<nav epub:type="toc"><ol>
  <li><a href="text/ch1.xhtml">Capítulo Um</a></li>
  <li><a href="text/ch2.xhtml">Capítulo Dois</a></li>
</ol></nav></body></html>"""

CH1 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Um</title>
<link rel="stylesheet" href="../styles/style.css"/>
<script>alert('xss')</script>
</head><body>
<h1>Capítulo Um</h1>
<p onerror="alert(1)">Olá mundo do leitor.</p>
<img src="../images/pic.png" onerror="alert(2)"/>
<a href="ch2.xhtml">próximo</a>
<a href="https://example.com/remote">remoto</a>
</body></html>"""

CH2 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Dois</title></head>
<body><h1>Capítulo Dois</h1><p>Fim.</p></body></html>"""

CSS = 'body{background:url("../images/pic.png");color:#222} @import url("https://remote/x.css");'


def _png(color: tuple[int, int, int], size: tuple[int, int] = (60, 90)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def build_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", OPF)
        archive.writestr("OEBPS/nav.xhtml", NAV)
        archive.writestr("OEBPS/text/ch1.xhtml", CH1)
        archive.writestr("OEBPS/text/ch2.xhtml", CH2)
        archive.writestr("OEBPS/styles/style.css", CSS)
        archive.writestr("OEBPS/images/pic.png", _png((10, 120, 200)))


def build_cbz(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("1.png", _png((255, 0, 0)))
        archive.writestr("2.png", _png((0, 255, 0)))
        archive.writestr("10.png", _png((0, 0, 255)))


def build_pdf(path: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    first = doc.new_page(width=200, height=300)
    first.draw_rect(pymupdf.Rect(0, 0, 200, 300), color=None, fill=(1, 0, 0))
    second = doc.new_page(width=200, height=300)
    second.draw_rect(pymupdf.Rect(0, 0, 200, 300), color=None, fill=(0, 0, 1))
    doc.save(str(path))
    doc.close()


def build_txt(path: Path) -> None:
    path.write_text(
        "CAPÍTULO 1\n\nEra uma vez um teste.\n\nCAPÍTULO 2\n\nFim da história.\n",
        encoding="utf-8",
    )


def build_fb2(path: Path) -> None:
    image = base64.b64encode(_png((200, 40, 40))).decode("ascii")
    path.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"
             xmlns:l="http://www.w3.org/1999/xlink">
  <body>
    <section>
      <title><p>Primeira Parte</p></title>
      <p>Texto com <emphasis>ênfase</emphasis>.</p>
      <image l:href="#img1"/>
      <poem><stanza><v>um verso</v></stanza></poem>
    </section>
  </body>
  <binary id="img1" content-type="image/png">{image}</binary>
</FictionBook>""",
        encoding="utf-8",
    )


def build_files() -> dict[str, Path]:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    files = {
        "epub": FIXTURES / "livro.epub",
        "cbz": FIXTURES / "quadrinho.cbz",
        "pdf": FIXTURES / "documento.pdf",
        "txt": FIXTURES / "texto.txt",
        "fb2": FIXTURES / "livro.fb2",
        "png": FIXTURES / "imagem.png",
        "djvu": FIXTURES / "escaneado.djvu",
    }
    build_epub(files["epub"])
    build_cbz(files["cbz"])
    build_pdf(files["pdf"])
    build_txt(files["txt"])
    build_fb2(files["fb2"])
    files["png"].write_bytes(_png((30, 30, 30), (120, 120)))
    files["djvu"].write_bytes(b"AT&TFORM\x00\x00\x00\x00DJVU" + b"\x00" * 64)
    return files


def upload(client, path: Path) -> str | None:
    response = client.post(
        "/api/imports/upload",
        files={"files": (path.name, path.read_bytes(), "application/octet-stream")},
        data={"category": "Testes"},
    )
    if response.status_code != 200:
        return None
    results = response.json().get("results") or []
    if not results or not results[0].get("book"):
        return None
    return results[0]["book"]["id"]


# ---------------------------------------------------------------------------
# direct (no server) checks
# ---------------------------------------------------------------------------
def unit_checks(files: dict[str, Path]) -> None:
    print("\n[seleção de handler]")
    from app.library.detect import detect
    from app.reader.registry import handler_for_format, select_handler

    expected = {
        "epub": "epub",
        "cbz": "comic",
        "pdf": "pdf",
        "txt": "text",
        "fb2": "fb2",
        "png": "image",
        "djvu": "document",
        # Kindle/DOCX/RTF são convertidos pelos conversores nativos do projeto;
        # só formatos exóticos como .lit dependem do Calibre (opcional).
        "mobi": "native_convert",
        "azw3": "native_convert",
        "docx": "native_convert",
        "lit": "calibre",
        "html": "markup",
        "mp3": "audio",
    }
    for ext, handler_name in expected.items():
        found = handler_for_format(ext)
        check(f"formato {ext} -> {handler_name}",
              found is not None and found.name == handler_name,
              found.name if found else "None")

    for key, handler_name in (("epub", "epub"), ("cbz", "comic"), ("pdf", "pdf"),
                              ("txt", "text"), ("fb2", "fb2"), ("png", "image")):
        detection = detect(files[key])
        book = SimpleNamespace(format=detection.format, reading_direction="ltr")
        handler = select_handler(book, detection)
        check(f"seleção por detecção: {key}",
              handler is not None and handler.name == handler_name,
              handler.name if handler else "None")

    print("\n[sanitização de HTML]")
    from app.reader.sanitize import (
        asset_url_factory,
        chapter_url_factory,
        sanitize_css,
        sanitize_html,
    )

    dirty = (
        b"<html><head><script>bad()</script><link rel='stylesheet' href='css/a.css'/></head>"
        b"<body onload='bad()'><p onclick='x'>hi</p>"
        b"<img src='img/p.png' onerror='bad()'/>"
        b"<a href='cap.xhtml'>c</a>"
        b"<a href='javascript:bad()'>j</a><a href='../../outside.png'>o</a>"
        b"<iframe src='https://evil'></iframe></body></html>"
    )
    clean = sanitize_html(
        dirty,
        base="text",
        asset_url=asset_url_factory("B1"),
        chapter_url=chapter_url_factory("B1"),
    )
    check("<script> removido", "<script" not in clean and "bad()" not in clean)
    check("on* removidos", "onerror" not in clean and "onload" not in clean and "onclick" not in clean)
    check("iframe/javascript: bloqueados", "<iframe" not in clean and "javascript:" not in clean)
    check("URL interna reescrita",
          "/reader/B1/asset/text/img/p.png" in clean, clean[:200])
    check("link de capítulo reescrito",
          "/reader/B1/chapter/text/cap.xhtml" in clean, clean[:200])
    check("escape de diretório bloqueado", "outside.png" not in clean)

    css = sanitize_css(
        'body{background:url("../images/p.png")} @import url("https://r/x.css");',
        base="styles",
        asset_url=asset_url_factory("B1"),
    )
    check("CSS: url interna reescrita", "/reader/B1/asset/images/p.png" in css, css)
    check("CSS: @import removido", "@import" not in css)

    print("\n[estado honesto / não suportado]")
    from app.reader.base import ReaderContext
    from app.reader.cache import ReaderCache
    from app.reader.document import UnsupportedHandler

    ctx = ReaderContext(
        book=SimpleNamespace(id="X", title="Teste", format="xyz", reading_direction="ltr",
                             media_type="", content_type="unknown", file_size=1),
        path=files["png"],
        detection=SimpleNamespace(format="xyz", content_type="unknown", page_count=None),
        format="xyz",
        cache=ReaderCache("X", None),
    )
    manifest = UnsupportedHandler().manifest(ctx)
    check("manifest não suportado", manifest["state"] == "unavailable" and manifest["message"])

    print("\n[cache por mtime/tamanho]")
    cache_dir = WORKDIR / "cache-fixture.bin"
    cache_dir.write_bytes(b"abc")
    calls = {"n": 0}

    def build(tmp: Path) -> None:
        calls["n"] += 1
        (tmp / "art.txt").write_text("ok", encoding="utf-8")

    first_cache = ReaderCache("BOOK", cache_dir)
    first_dir = first_cache.ensure(build)
    second_dir = first_cache.ensure(build)
    check("cache construído uma única vez", calls["n"] == 1 and first_dir == second_dir)
    check("artefato gerado", (first_dir / "art.txt").exists())
    os.utime(cache_dir, (time.time() + 10, time.time() + 10))
    third_dir = ReaderCache("BOOK", cache_dir).ensure(build)
    check("cache invalidado ao mudar o arquivo", calls["n"] == 2 and third_dir != first_dir)

    print("\n[ordem natural do CBZ]")
    from app.reader.comic import ComicHandler

    names = ComicHandler()._image_names("B", files["cbz"])
    check("páginas ordenadas naturalmente",
          names == ["1.png", "2.png", "10.png"], str(names))


# ---------------------------------------------------------------------------
# HTTP checks
# ---------------------------------------------------------------------------
def http_checks(client, ids: dict[str, str], files: dict[str, Path]) -> None:
    print("\n[página do leitor]")
    response = client.get(f"/reader/{ids['epub']}")
    check("GET /reader/{id} renderiza", response.status_code == 200 and "Web Reader" in response.text,
          str(response.status_code))
    check("botão Voltar ao livro presente", "Voltar ao livro" in response.text)
    check("assets do leitor versionados", "reader.css?v=" in response.text and "reader.js?v=" in response.text)

    response = client.get("/reader/nao-existe")
    check("livro inexistente -> 404", response.status_code == 404)

    print("\n[manifest por formato]")
    manifests: dict[str, dict] = {}
    for key in ("epub", "cbz", "pdf", "txt", "fb2", "png", "djvu"):
        response = client.get(f"/reader/{ids[key]}/manifest")
        payload = response.json()
        manifests[key] = payload
        check(f"manifest {key} responde JSON", response.status_code == 200 and "book" in payload)

    check("epub: capítulos + assets",
          manifests["epub"]["kind"] == "epub" and manifests["epub"]["state"] == "ready"
          and len(manifests["epub"]["chapters"]) == 2
          and manifests["epub"]["capabilities"]["assets"] is True,
          str(manifests["epub"].get("chapters"))[:120])
    check("cbz: 3 páginas", manifests["cbz"]["kind"] == "comic" and manifests["cbz"]["pages"] == 3,
          str(manifests["cbz"].get("pages")))
    check("pdf: 2 páginas imagem", manifests["pdf"]["kind"] == "pdf" and manifests["pdf"]["pages"] == 2,
          str(manifests["pdf"].get("pages")))
    check("txt: capítulos detectados", manifests["txt"]["kind"] == "text"
          and len(manifests["txt"]["chapters"]) == 2, str(manifests["txt"].get("chapters")))
    check("fb2: capítulo com assets", manifests["fb2"]["kind"] == "fb2"
          and len(manifests["fb2"]["chapters"]) == 1)
    check("imagem: 1 página", manifests["png"]["kind"] == "image" and manifests["png"]["pages"] == 1)
    check("djvu: estado honesto", manifests["djvu"]["state"] == "unavailable"
          and "DjVu" in (manifests["djvu"]["message"] or ""), str(manifests["djvu"])[:160])
    check("manifest lista presets e perfis",
          len(manifests["epub"]["presets"]) == 3 and len(manifests["epub"]["profiles"]) >= 10)

    print("\n[EPUB: capítulo sanitizado e assets]")
    chapter = manifests["epub"]["chapters"][0]["key"]
    response = client.get(f"/reader/{ids['epub']}/chapter/{chapter}")
    body = response.text
    check("capítulo respondido", response.status_code == 200 and "Olá mundo" in body)
    check("script do livro removido", "<script" not in body and "alert(" not in body)
    check("atributo on* removido", "onerror" not in body)
    check("imagem do livro reescrita", "/asset/OEBPS/images/pic.png" in body)
    check("link remoto bloqueado", "example.com" not in body)
    check("CSS do livro vinculado", "/asset/OEBPS/styles/style.css" in body)

    response = client.get(f"/reader/{ids['epub']}/asset/OEBPS/styles/style.css")
    check("CSS servido e reescrito",
          response.status_code == 200 and "/asset/OEBPS/images/pic.png" in response.text
          and "@import" not in response.text, response.text[:160])

    response = client.get(f"/reader/{ids['epub']}/asset/OEBPS/images/pic.png")
    check("imagem servida com MIME", response.status_code == 200
          and response.headers["content-type"].startswith("image/png"))

    print("\n[FB2: imagem embutida]")
    response = client.get(f"/reader/{ids['fb2']}/asset/binary/img1")
    check("imagem <binary> servida", response.status_code == 200
          and response.content.startswith(b"\x89PNG"))

    print("\n[contenção de caminho]")
    probes = [
        f"/reader/{ids['epub']}/asset/..%2F..%2Fopds.db",
        f"/reader/{ids['epub']}/asset/%2e%2e/%2e%2e/opds.db",
        f"/reader/{ids['epub']}/asset/..%2F..%2F..%2Fwindows/win.ini",
        f"/reader/{ids['epub']}/chapter/..%2F..%2Fopds.db",
    ]
    for probe in probes:
        response = client.get(probe)
        leaked = "SQLite format 3" in response.text or "[extensions]" in response.text
        check(f"bloqueado: {probe.split('/asset/')[-1]}",
              response.status_code in (400, 403, 404) and not leaked,
              f"{response.status_code}")

    response = client.get(f"/reader/{ids['cbz']}/page/999")
    check("página fora do intervalo -> 404", response.status_code == 404)

    print("\n[quadrinhos: páginas na ordem certa]")
    from PIL import Image

    colors = []
    for index in range(3):
        response = client.get(f"/reader/{ids['cbz']}/page/{index}")
        image = Image.open(io.BytesIO(response.content)).convert("RGB")
        colors.append(image.getpixel((5, 5)))
    check("página 0 vermelha", colors[0][0] > 200 and colors[0][1] < 60, str(colors[0]))
    check("página 1 verde", colors[1][1] > 200 and colors[1][0] < 60, str(colors[1]))
    check("página 2 azul (10.png depois de 2.png)", colors[2][2] > 200 and colors[2][0] < 60, str(colors[2]))

    print("\n[PDF: modo imagem]")
    response = client.get(f"/reader/{ids['pdf']}/page/0?w=200")
    check("página PDF renderizada",
          response.status_code == 200 and response.headers["content-type"].startswith("image/"))
    response = client.get(f"/reader/{ids['pdf']}/page/1?w=200&gray=1")
    check("página PDF em cinza (png)",
          response.status_code == 200 and response.headers["content-type"] == "image/png")

    print("\n[raw inline]")
    response = client.get(f"/reader/{ids['epub']}/raw")
    check("raw EPUB inline com MIME",
          response.status_code == 200
          and response.headers["content-type"].startswith("application/epub+zip")
          and "inline" in response.headers.get("content-disposition", ""),
          response.headers.get("content-type", ""))


def main() -> int:
    from fastapi.testclient import TestClient

    from app.main import create_app

    files = build_files()
    unit_checks(files)

    app = create_app()
    with TestClient(app) as client:
        ids: dict[str, str] = {}
        for key, path in files.items():
            book_id = upload(client, path)
            if book_id:
                ids[key] = book_id
        check("fixtures importadas", len(ids) == len(files), f"{sorted(ids)}")
        if len(ids) == len(files):
            http_checks(client, ids, files)

    shutil.rmtree(WORKDIR, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
