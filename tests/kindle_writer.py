"""Native Kindle writer tests (AZW3/KF8 and legacy MOBI), no Calibre.

Run with:  python tests/kindle_writer.py

Builds an EPUB on the fly, converts it with ``app.converters.native.kindle``,
validates the PalmDB/MOBI headers and, when available, reads the result back
with the independent ``mobi`` (KindleUnpack) package.
"""

from __future__ import annotations

import re
import shutil
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORK = Path(tempfile.mkdtemp(prefix="opds_kindle_"))

PASSED: list[str] = []
FAILED: list[str] = []

CHAPTER_ONE = "Olá mundo do leitor, com acentuação correta."
CHAPTER_TWO = "Segundo capítulo, texto final do livro."

CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Livro Nativo</dc:title>
    <dc:creator>Fulano de Tal</dc:creator>
    <dc:language>pt-BR</dc:language>
    <dc:publisher>Editora Teste</dc:publisher>
    <dc:description>Um livro de teste.</dc:description>
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
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Índice</title></head>
<body><nav><ol><li><a href="text/ch1.xhtml">Capítulo Um</a></li></ol></nav></body></html>"""

CH1 = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Um</title>
<link rel="stylesheet" href="../styles/style.css"/></head><body>
<h1>Capítulo Um</h1>
<p>{CHAPTER_ONE}</p>
<img src="../images/pic.png" alt="Foto"/>
</body></html>"""

CH2 = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Dois</title></head>
<body><h1>Capítulo Dois</h1><p>{CHAPTER_TWO}</p></body></html>"""

CSS = "body { color: #222; } h1 { margin: 1em 0; }"

# --- illustrated fixture (3 chapters + a dedicated cover) -------------------
CHAPTER_TITLES = ["Capítulo Um", "Capítulo Dois", "Capítulo Três"]
COVER_SIZE = (200, 300)

ILLUSTRATED_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Livro Ilustrado</dc:title>
    <dc:creator>Fulano de Tal</dc:creator>
    <dc:language>pt-BR</dc:language>
    <meta name="cover" content="cover-img"/>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="c3" href="text/ch3.xhtml" media-type="application/xhtml+xml"/>
    <item id="cover-img" href="images/cover.png" media-type="image/png" properties="cover-image"/>
    <item id="img" href="images/pic.png" media-type="image/png"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/><itemref idref="c3"/></spine>
</package>"""


def _chapter(title: str, text: str, image: str = "") -> str:
    pict = f'<img src="../images/{image}" alt="foto"/>' if image else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        f"<title>{title}</title></head><body>"
        f"<h1>{title}</h1><p>{text}</p>{pict}</body></html>"
    )



def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def _png(color: tuple[int, int, int], size: tuple[int, int] = (60, 90)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
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


def build_illustrated_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", ILLUSTRATED_OPF)
        archive.writestr("OEBPS/nav.xhtml", NAV)
        archive.writestr("OEBPS/text/ch1.xhtml", _chapter(CHAPTER_TITLES[0], CHAPTER_ONE))
        archive.writestr("OEBPS/text/ch2.xhtml", _chapter(CHAPTER_TITLES[1], CHAPTER_TWO, "pic.png"))
        archive.writestr("OEBPS/text/ch3.xhtml", _chapter(CHAPTER_TITLES[2], "Terceiro capítulo."))
        archive.writestr("OEBPS/images/cover.png", _png((200, 20, 20), COVER_SIZE))
        archive.writestr("OEBPS/images/pic.png", _png((10, 120, 200)))



# --- PalmDB parsing helpers -------------------------------------------------
def split_records(data: bytes) -> list[bytes]:
    count = struct.unpack_from(">H", data, 76)[0]
    offsets = [struct.unpack_from(">L", data, 78 + 8 * i)[0] for i in range(count)]
    offsets.append(len(data))
    return [data[offsets[i] : offsets[i + 1]] for i in range(count)]


def mobi_field(record0: bytes, offset: int) -> int:
    """Read a u32 field at a MOBI-relative offset (record 0 starts with PalmDOC)."""
    return struct.unpack_from(">L", record0, 16 + offset)[0]


def parse_exth(record0: bytes) -> dict[int, bytes]:
    length = mobi_field(record0, 0x04)
    start = 16 + length
    if record0[start : start + 4] != b"EXTH":
        return {}
    _size, count = struct.unpack_from(">LL", record0, start + 4)
    position = start + 12
    entries: dict[int, bytes] = {}
    for _ in range(count):
        kind, size = struct.unpack_from(">LL", record0, position)
        entries[kind] = record0[position + 8 : position + size]
        position += size
    return entries


def record0_title(record0: bytes) -> str:
    offset = mobi_field(record0, 0x44)
    length = mobi_field(record0, 0x48)
    return record0[offset : offset + length].decode("utf-8", "replace")


def raw_text(record0: bytes, records: list[bytes]) -> bytes:
    count = struct.unpack_from(">H", record0, 8)[0]
    return b"".join(records[1 : 1 + count])


def has_image(records: list[bytes]) -> bool:
    return any(
        record[:2] in (b"\xff\xd8", b"\x89P") or record[:3] == b"\xff\xd8\xff"
        for record in records
    )


def is_jpeg(record: bytes) -> bool:
    return record[:2] == b"\xff\xd8"


def image_size(data: bytes) -> tuple[int, int]:
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        return image.size


def ncx_titles(text: str) -> list[str]:
    """Pull the navigation labels of an NCX (or EPUB3 nav) in document order."""
    labels = re.findall(r"<navLabel>\s*<text>(.*?)</text>", text, re.DOTALL)
    if labels:
        return [label.strip() for label in labels]
    return [href.strip() for href in re.findall(r"<a[^>]*>(.*?)</a>", text, re.DOTALL)]



# --- checks -----------------------------------------------------------------
def headers_test(name: str, data: bytes, version: int) -> None:
    records = split_records(data)
    record0 = records[0]
    check(f"{name}: assinatura BOOKMOBI no offset 60", data[60:68] == b"BOOKMOBI", repr(data[60:68]))
    check(f"{name}: tem registros", len(records) >= 4, str(len(records)))
    check(f"{name}: versão MOBI = {version}", mobi_field(record0, 0x14) == version, str(mobi_field(record0, 0x14)))
    check(f"{name}: codificação UTF-8", mobi_field(record0, 0x0C) == 65001, str(mobi_field(record0, 0x0C)))
    check(f"{name}: tipo livro", mobi_field(record0, 0x08) == 2, str(mobi_field(record0, 0x08)))
    check(f"{name}: título no registro 0", record0_title(record0) == "Livro Nativo", record0_title(record0))
    exth = parse_exth(record0)
    check(f"{name}: autor no EXTH", exth.get(100) == b"Fulano de Tal", str(exth.get(100)))
    check(f"{name}: editora no EXTH", exth.get(101) == b"Editora Teste", str(exth.get(101)))


def azw3_test(epub: Path) -> None:
    from app.converters.native.kindle import epub_to_kindle

    out = WORK / "livro.azw3"
    epub_to_kindle(epub, out, target="azw3", title="Livro Nativo", author="Fulano de Tal")
    data = out.read_bytes()
    records = split_records(data)
    headers_test("azw3", data, 8)
    check("azw3: arquivo gerado", out.exists() and len(data) > 1000, str(len(data)))

    text = raw_text(records[0], records)
    check("azw3: texto do capítulo 1 no rawML", CHAPTER_ONE.encode() in text)
    check("azw3: texto do capítulo 2 no rawML", CHAPTER_TWO.encode() in text)
    check("azw3: CSS embutido", b"color: #222" in text)
    check("azw3: imagem referenciada por kindle:embed", b"kindle:embed:0001" in text)
    check("azw3: registro de imagem presente", has_image(records))

    _roundtrip_kf8(out)


def _roundtrip_kf8(out: Path) -> None:
    try:
        from mobi import extract
    except Exception as exc:  # noqa: BLE001
        check("azw3: leitura de volta (pacote mobi instalado)", False, str(exc))
        return
    try:
        tmpdir, epub_path = extract(str(out))
    except Exception as exc:  # noqa: BLE001
        check("azw3: leitura de volta concluída", False, repr(exc))
        return
    try:
        with zipfile.ZipFile(epub_path) as archive:
            names = archive.namelist()
            xhtml = "".join(
                archive.read(name).decode("utf-8", "replace")
                for name in names
                if name.lower().endswith((".xhtml", ".html"))
            )
            opf = next((n for n in names if n.lower().endswith(".opf")), None)
            opf_text = archive.read(opf).decode("utf-8", "replace") if opf else ""
            images = [n for n in names if n.lower().endswith((".jpg", ".jpeg", ".png"))]
            image_ok = False
            if images:
                from io import BytesIO

                from PIL import Image

                with Image.open(BytesIO(archive.read(images[0]))) as image:
                    image_ok = image.size == (60, 90)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    check("azw3: capítulo 1 sobreviveu ao round-trip", CHAPTER_ONE in xhtml)
    check("azw3: capítulo 2 sobreviveu ao round-trip", CHAPTER_TWO in xhtml)
    check("azw3: título no OPF reconstruído", "<dc:title>Livro Nativo</dc:title>" in opf_text)
    check("azw3: autor no OPF reconstruído", "<dc:creator>Fulano de Tal</dc:creator>" in opf_text)
    check("azw3: imagem reconstruída com as dimensões originais", image_ok)


def azw3_cover_ncx_test(epub: Path) -> None:
    from app.converters.native.kindle import epub_to_kindle

    out = WORK / "ilustrado.azw3"
    epub_to_kindle(epub, out, target="azw3", title="Livro Ilustrado", author="Fulano de Tal")
    data = out.read_bytes()
    records = split_records(data)
    record0 = records[0]
    exth = parse_exth(record0)
    first_image = mobi_field(record0, 0x5C)

    check("azw3 capa: EXTH 201 presente", 201 in exth, str(exth.get(201)))
    check("azw3 capa: EXTH 202 presente", 202 in exth, str(exth.get(202)))
    if 201 not in exth or 202 not in exth:
        return
    cover_offset = struct.unpack(">L", exth[201])[0]
    thumb_offset = struct.unpack(">L", exth[202])[0]
    check("azw3 capa: offsets distintos", cover_offset != thumb_offset, f"{cover_offset}/{thumb_offset}")
    check(
        "azw3 capa: EXTH 201 aponta para imagem JPEG",
        is_jpeg(records[first_image + cover_offset]),
        str(first_image + cover_offset),
    )
    check(
        "azw3 capa: EXTH 202 aponta para imagem JPEG",
        is_jpeg(records[first_image + thumb_offset]),
        str(first_image + thumb_offset),
    )
    check(
        "azw3 sumário: ncx_index definido",
        mobi_field(record0, 0xE4) != 0xFFFFFFFF,
        hex(mobi_field(record0, 0xE4)),
    )

    _roundtrip_cover_ncx(out)


def _roundtrip_cover_ncx(out: Path) -> None:
    try:
        from mobi import extract
    except Exception as exc:  # noqa: BLE001
        check("azw3 capa: leitura de volta (pacote mobi instalado)", False, str(exc))
        return
    try:
        tmpdir, epub_path = extract(str(out))
    except Exception as exc:  # noqa: BLE001
        check("azw3 capa: leitura de volta concluída", False, repr(exc))
        return
    try:
        with zipfile.ZipFile(epub_path) as archive:
            names = archive.namelist()
            image_names = [n for n in names if n.lower().endswith((".jpg", ".jpeg", ".png"))]
            cover = next((n for n in image_names if Path(n).name.startswith("cover")), None)
            cover_size = image_size(archive.read(cover)) if cover else None
            toc_name = next((n for n in names if n.lower().endswith(".ncx")), None)
            if toc_name is None:
                toc_name = next(
                    (n for n in names if "nav" in Path(n).name.lower() and n.lower().endswith(".xhtml")),
                    None,
                )
            titles = (
                ncx_titles(archive.read(toc_name).decode("utf-8", "replace")) if toc_name else []
            )
            opf = next((n for n in names if n.lower().endswith(".opf")), None)
            opf_text = archive.read(opf).decode("utf-8", "replace") if opf else ""
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    check("azw3 capa: capa no EPUB reconstruído", cover is not None, str(image_names))
    check("azw3 capa: dimensões da capa conferem", cover_size == COVER_SIZE, str(cover_size))
    check(
        "azw3 capa: capa marcada no OPF",
        'name="cover"' in opf_text or 'properties="cover-image"' in opf_text,
        opf_text[:200],
    )
    check("azw3 sumário: NCX/nav presente", bool(titles), str(titles))
    check("azw3 sumário: títulos dos capítulos na ordem", titles == CHAPTER_TITLES, str(titles))


def mobi_test(epub: Path) -> None:
    from app.converters.native.kindle import epub_to_kindle

    out = WORK / "livro.mobi"
    epub_to_kindle(epub, out, target="mobi", title="Livro Nativo", author="Fulano de Tal")
    data = out.read_bytes()
    records = split_records(data)
    headers_test("mobi", data, 6)

    text = raw_text(records[0], records)
    check("mobi: texto do capítulo 1", CHAPTER_ONE.encode() in text)
    check("mobi: texto do capítulo 2", CHAPTER_TWO.encode() in text)
    check("mobi: imagem referenciada por recindex", b'recindex="00001"' in text)
    check("mobi: registro de imagem presente", has_image(records))

    _roundtrip_mobi6(out)


def _roundtrip_mobi6(out: Path) -> None:
    try:
        from mobi import extract
    except Exception as exc:  # noqa: BLE001
        check("mobi: leitura de volta (pacote mobi instalado)", False, str(exc))
        return
    try:
        tmpdir, html_path = extract(str(out))
    except Exception as exc:  # noqa: BLE001
        check("mobi: leitura de volta concluída", False, repr(exc))
        return
    try:
        html = Path(html_path).read_text(encoding="utf-8", errors="replace")
        images_dir = Path(html_path).parent / "Images"
        images = list(images_dir.glob("*")) if images_dir.is_dir() else []
        cover = next((p for p in images if p.name.startswith("cover")), None)
        cover_size = image_size(cover.read_bytes()) if cover else None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    check("mobi: capítulo 1 sobreviveu ao round-trip", CHAPTER_ONE in html)
    check("mobi: capítulo 2 sobreviveu ao round-trip", CHAPTER_TWO in html)
    check("mobi: imagem reconstruída", len(images) == 2, str([p.name for p in images]))
    check("mobi: capa reconstruída com as dimensões originais", cover_size == (60, 90), str(cover_size))


def failure_test(epub: Path) -> None:
    from app.converters.native.kindle import KindleConversionError, epub_to_kindle

    missing = WORK / "nao-existe.epub"
    try:
        epub_to_kindle(missing, WORK / "x.azw3", target="azw3")
        check("entrada inexistente falha claramente", False, "não levantou")
    except KindleConversionError:
        check("entrada inexistente falha claramente", True)
    except Exception as exc:  # noqa: BLE001
        check("entrada inexistente falha claramente", False, repr(exc))

    bogus = WORK / "falso.epub"
    bogus.write_bytes(b"isto nao e um epub")
    try:
        epub_to_kindle(bogus, WORK / "y.azw3", target="azw3")
        check("arquivo que não é EPUB falha claramente", False, "não levantou")
    except KindleConversionError:
        check("arquivo que não é EPUB falha claramente", True)
    except Exception as exc:  # noqa: BLE001
        check("arquivo que não é EPUB falha claramente", False, repr(exc))

    try:
        epub_to_kindle(epub, WORK / "z.xyz", target="pdf")
        check("alvo desconhecido falha claramente", False, "não levantou")
    except KindleConversionError:
        check("alvo desconhecido falha claramente", True)
    except Exception as exc:  # noqa: BLE001
        check("alvo desconhecido falha claramente", False, repr(exc))


def main() -> int:
    from app.converters.native.kindle import kindle_available

    check("backend nativo disponível", kindle_available() is True)

    epub = WORK / "fonte.epub"
    build_epub(epub)
    check("EPUB de teste montado", epub.exists() and epub.stat().st_size > 0)

    print("\n[EPUB -> AZW3 (KF8)]")
    azw3_test(epub)

    print("\n[EPUB -> MOBI (legado)]")
    mobi_test(epub)

    illustrated = WORK / "ilustrado.epub"
    build_illustrated_epub(illustrated)
    check("EPUB ilustrado de teste montado", illustrated.exists() and illustrated.stat().st_size > 0)

    print("\n[AZW3 com capa e sumário]")
    azw3_cover_ncx_test(illustrated)

    print("\n[falhas claras]")
    failure_test(epub)

    shutil.rmtree(WORK, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
