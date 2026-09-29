"""CrossPoint (Xteink) OPDS compatibility test.

This replicates the *actual* rules of CrossPoint's ``lib/OpdsParser/OpdsParser.cpp``
against our feed, using the same parser library (expat). If this passes, the
Xteink will list the books.

CrossPoint rules that matter:
  * an <entry> is kept only if it has a non-empty title AND a non-empty href;
  * a book link needs rel containing "opds-spec.org/acquisition" AND
    type EXACTLY "application/epub+zip" (EPUB is a ZIP, so announcing
    "application/zip" hides every book);
  * a navigation link needs type containing "application/atom+xml";
  * the download filename comes from <title> + <author><name>, plus ".epub".

Run with:  python tests/crosspoint_compat.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.parsers import expat

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="opds_crosspoint_"))
os.environ["DATA_DIR"] = str(WORKDIR)
os.environ["SECRET_KEY"] = "crosspoint-test"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["RSS_WORKER_ENABLED"] = "false"
os.environ["BASE_URL"] = "http://testserver"
os.environ["REQUIRE_AUTH_PANEL"] = "false"

PASSED: list[str] = []
FAILED: list[str] = []

EPUB_TYPE = "application/epub+zip"
ATOM_TYPE = "application/atom+xml"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


class CrossPointParser:
    """Port of OpdsParser's element handling (BOOK vs NAVIGATION)."""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        self._current: dict | None = None
        self._text = ""
        self._in = {"title": False, "author": False, "name": False, "id": False}
        self.parse_error: str | None = None

    def parse(self, data: bytes) -> None:
        parser = expat.ParserCreate()
        parser.StartElementHandler = self._start
        parser.EndElementHandler = self._end
        parser.CharacterDataHandler = self._chars
        try:
            parser.Parse(data, True)
        except expat.ExpatError as exc:
            self.parse_error = f"{exc} (line {exc.lineno})"

    # -- handlers ---------------------------------------------------------
    def _start(self, name: str, attrs: dict) -> None:
        if name == "entry" or name.endswith(":entry"):
            self._current = {"type": None, "title": "", "author": "", "href": "", "id": ""}
            self._text = ""
            self._in = {"title": False, "author": False, "name": False, "id": False}
            return

        if (name == "link" or name.endswith(":link")) and self._current is not None:
            href = attrs.get("href")
            rel = attrs.get("rel", "")
            type_ = attrs.get("type")
            if href and rel and type_ and "opds-spec.org/acquisition" in rel and type_ == EPUB_TYPE:
                # Mirrors OpdsParser.cpp: the first EPUB link wins; a later
                # "plain" .epub link only replaces a non-plain one.
                is_plain = ".epub" in href or "/epub/" in href
                current = self._current
                already_plain = current["type"] == "book" and (
                    ".epub" in current["href"] or "/epub/" in current["href"]
                )
                if current["type"] != "book" or (is_plain and not already_plain):
                    current["type"] = "book"
                    current["href"] = href
            elif href and type_ and ATOM_TYPE in type_ and self._current["type"] != "book":
                self._current["type"] = "navigation"
                self._current["href"] = href
            return

        if self._current is None:
            return
        if name == "title" or name.endswith(":title"):
            self._in["title"] = True
            self._text = ""
        elif name == "author" or name.endswith(":author"):
            self._in["author"] = True
        elif name == "name" or name.endswith(":name"):
            if self._in["author"]:
                self._in["name"] = True
                self._text = ""
        elif name == "id" or name.endswith(":id"):
            self._in["id"] = True
            self._text = ""

    def _end(self, name: str) -> None:
        if name == "entry" or name.endswith(":entry"):
            if self._current is not None:
                if self._current["title"] and self._current["href"]:
                    entry = dict(self._current)
                    entry["type"] = entry["type"] or "navigation"
                    self.entries.append(entry)
                self._current = None
            return
        if self._current is None:
            return
        if name == "title" or name.endswith(":title"):
            if self._in["title"]:
                self._current["title"] = self._text
            self._in["title"] = False
        elif name == "author" or name.endswith(":author"):
            self._in["author"] = False
        elif (name == "name" or name.endswith(":name")) and self._in["name"]:
            self._current["author"] = self._text
            self._in["name"] = False
        elif name == "id" or name.endswith(":id"):
            if self._in["id"]:
                self._current["id"] = self._text
            self._in["id"] = False

    def _chars(self, text: str) -> None:
        if self._current is None:
            return
        if self._in["title"] or self._in["name"] or self._in["id"]:
            self._text += text

    @property
    def books(self) -> list[dict]:
        return [e for e in self.entries if e["type"] == "book"]

    @property
    def navigation(self) -> list[dict]:
        return [e for e in self.entries if e["type"] == "navigation"]


# --- fixtures -------------------------------------------------------------
def make_cbz() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for index in range(3):
            img = Image.new("RGB", (300, 450), (30 * index, 90, 160))
            page = BytesIO()
            img.save(page, "PNG")
            zf.writestr(f"{index:03d}.png", page.getvalue())
    return buffer.getvalue()


def make_epub() -> bytes:

    from PIL import Image

    from app.converters.epub import EpubMeta, write_epub

    tmp = WORKDIR / "fixture"
    tmp.mkdir(parents=True, exist_ok=True)
    images = []
    for index in range(2):
        img = Image.new("RGB", (300, 450), (180, 60, 90))
        path = tmp / f"{index}.png"
        img.save(path)
        images.append(path)
    out = tmp / "manga_teste.epub"
    write_epub(out, images=images, meta=EpubMeta(title="Manga Teste", author="Autor Teste"))
    return out.read_bytes()


def main() -> int:
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        # Primeiro acesso: cria a credencial do painel e sai (para testar o login).
        from app.security import setup as _setup  # o token vem do proprio servidor
        client.post("/setup", data={"username": "admin", "password": "test-password",
                                   "confirm_password": "test-password", "token": _setup.token()})
        client.get("/logout")

        # O catalogo agora exige credencial por padrao; aqui interessa o conteudo.
        from app.database.base import session_scope as _scope
        from app.security import runtime as _runtime

        with _scope() as _session:
            _runtime.save(_session, {"opds_require_auth": False})

        # Import one comic and one ebook.
        response = client.post(
            "/api/imports/upload",
            files={"files": ("volume1.cbz", make_cbz(), "application/vnd.comicbook+zip")},
        )
        check("CBZ importado", response.json().get("imported") == 1, str(response.json())[:160])

        response = client.post(
            "/api/imports/upload",
            files={"files": ("manga_teste.epub", make_epub(), EPUB_TYPE)},
        )
        check("EPUB importado", response.json().get("imported") == 1, str(response.json())[:160])

        # A raiz depende de OPDS_ROOT_MODE: por padrão mostra as seções. Os
        # catálogos que entregam livros (e que o leitor simples deve usar) são
        # /opds/all, /opds/new e /opds/device/{slug}.
        for path in ("/opds/all", "/opds/new", "/opds/device/xteink_x4_pro"):
            print(f"\n[{path}]")
            raw = client.get(path).content

            parser = CrossPointParser()
            parser.parse(raw)
            if parser.parse_error:
                check("XML válido para expat", False, parser.parse_error)
                continue
            check("XML válido para expat", True)

            books = parser.books
            check("CrossPoint encontra livros", len(books) >= 1, f"{len(books)} livros, {len(parser.entries)} entradas")

            for book in books:
                check(f'título não vazio ("{book["title"][:28]}")', bool(book["title"]))
                check("href não vazio", bool(book["href"]))
            print(f"       livros: {[(b['title'][:30], b['href'].rsplit('/',1)[-1][:8]) for b in books]}")
            print(f"       navegação: {len(parser.navigation)}")

        # The download must carry the exact EPUB type and an .epub filename.
        print("\n[download]")
        response = client.get("/opds/all")
        parser = CrossPointParser()
        parser.parse(response.content)
        if parser.books:
            download = client.get(parser.books[0]["href"])
            check("download HTTP 200", download.status_code == 200, str(download.status_code))
            check(
                "Content-Type do download é application/epub+zip",
                download.headers.get("content-type", "").startswith(EPUB_TYPE),
                download.headers.get("content-type", ""),
            )
            disposition = download.headers.get("content-disposition", "")
            check("nome do arquivo termina em .epub", ".epub" in disposition, disposition)

    shutil.rmtree(WORKDIR, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
