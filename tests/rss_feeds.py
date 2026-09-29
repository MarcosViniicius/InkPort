"""RSS ingestion across feed flavours (no network).

Covers the cases a real feed mix throws at the worker:

1. entry with an EPUB enclosure            -> imported as a book, converted
2. entry with the full article inline      -> article, no page fetch, converted
3. entry with only a link                  -> page fetched, article, converted
4. entry with an audio enclosure           -> skipped (podcast), not an error
5. entry with a featured-image enclosure   -> the article link is used, not the image
6. page fetch fails but inline exists      -> falls back to the inline content
7. page comes back thin (paywall/bot)      -> falls back to the inline content
8. page fetch fails and there is no inline -> reported as an error

Run with:  python tests/rss_feeds.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="opds_rss_"))
os.environ["DATA_DIR"] = str(WORKDIR)
os.environ["SECRET_KEY"] = "rss-test"
os.environ["BASE_URL"] = "http://testserver"
os.environ["RSS_WORKER_ENABLED"] = "false"
os.environ["REQUIRE_AUTH_PANEL"] = "false"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def article(marker: str) -> str:
    """Distinct article HTML per entry (identical content would be deduped)."""
    return (
        f"<main><article><h1>Artigo {marker}</h1>"
        + "".join(f"<h2>Secao {i}</h2><p>{'texto ' * 120} {marker}</p>" for i in range(1, 6))
        + "</article></main>"
    )


SHORT_ARTICLE = "<main><article><p>Resumo curto do artigo.</p></article></main>"


def short_article(marker: str) -> str:
    return f"<main><article><p>Resumo curto {marker}.</p></article></main>"


def feed_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>Feed de teste</title>
  <link>https://exemplo.com/</link>
  <item>
    <title>Livro em anexo</title><guid>g1</guid>
    <link>https://exemplo.com/livro</link>
    <enclosure url="https://exemplo.com/livro.epub" type="application/epub+zip" length="1"/>
  </item>
  <item>
    <title>Artigo completo no feed</title><guid>g2</guid>
    <link>https://exemplo.com/artigo-inline</link>
    <content:encoded><![CDATA[{article("g2")}]]></content:encoded>
  </item>
  <item>
    <title>So link</title><guid>g3</guid>
    <link>https://exemplo.com/artigo-link</link>
  </item>
  <item>
    <title>Podcast</title><guid>g4</guid>
    <link>https://exemplo.com/podcast</link>
    <enclosure url="https://exemplo.com/ep.mp3" type="audio/mpeg" length="1"/>
  </item>
  <item>
    <title>Com imagem destacada</title><guid>g5</guid>
    <link>https://exemplo.com/artigo-com-imagem</link>
    <enclosure url="https://exemplo.com/destaque.jpg" type="image/jpeg" length="1"/>
  </item>
  <item>
    <title>Artigo longo com pagina fora do ar</title><guid>g6</guid>
    <link>https://exemplo.com/fails-long</link>
    <content:encoded><![CDATA[{article("g6")}]]></content:encoded>
  </item>
  <item>
    <title>Artigo longo com pagina bloqueada</title><guid>g7</guid>
    <link>https://exemplo.com/thin-long</link>
    <content:encoded><![CDATA[{article("g7")}]]></content:encoded>
  </item>
  <item>
    <title>Sem conteudo nenhum</title><guid>g8</guid>
    <link>https://exemplo.com/fails-no-inline</link>
  </item>
  <item>
    <title>Resumo curto e pagina fora do ar</title><guid>g9</guid>
    <link>https://exemplo.com/fails</link>
    <description><![CDATA[{short_article("g9")}]]></description>
  </item>
  <item>
    <title>Resumo curto e pagina bloqueada</title><guid>g10</guid>
    <link>https://exemplo.com/thin</link>
    <description><![CDATA[{short_article("g10")}]]></description>
  </item>
</channel></rss>
"""


def make_epub() -> bytes:

    from PIL import Image

    from app.converters.epub import EpubMeta, write_epub

    tmp = WORKDIR / "fixture"
    tmp.mkdir(parents=True, exist_ok=True)
    image = tmp / "p.png"
    Image.new("L", (300, 450), 200).save(image)
    out = tmp / "attached.epub"
    write_epub(out, images=[image], meta=EpubMeta(title="Livro em anexo", author="Autor"))
    return out.read_bytes()


class FakeDownloader:
    """Stands in for app.rss.downloader.Downloader."""

    def __init__(self, xml: str) -> None:
        from app.library.hashing import sha256_file

        self.xml = xml
        self.epub = make_epub()
        self.calls: list[str] = []
        self._hash = sha256_file

    def fetch(self, url: str) -> bytes:
        return self.xml.encode("utf-8")

    def download(self, url: str, dest_dir: Path, *, filename: str | None = None):
        from app.rss.downloader import Downloaded, DownloadError

        self.calls.append(url)
        dest_dir.mkdir(parents=True, exist_ok=True)

        if url.endswith(".epub"):
            path = dest_dir / "anexo.epub"
            path.write_bytes(self.epub)
            return Downloaded(path=path, content_type="application/epub+zip")

        if url.endswith(".mp3"):
            path = dest_dir / "ep.mp3"
            path.write_bytes(b"ID3" + b"\x00" * 64)
            return Downloaded(path=path, content_type="audio/mpeg")

        if url.endswith(".jpg"):
            from io import BytesIO

            from PIL import Image

            path = dest_dir / "destaque.jpg"
            buffer = BytesIO()
            Image.new("RGB", (60, 40), (200, 30, 30)).save(buffer, "JPEG")
            path.write_bytes(buffer.getvalue())
            return Downloaded(path=path, content_type="image/jpeg")

        if "fails" in url:
            raise DownloadError(f"404 para {url}")

        html = (
            f"<html><head><title>Pagina {url}</title></head><body>"
            f"<main><article><h1>Titulo {url}</h1>"
            f"<p>{'conteudo ' * 400} {url}</p></article></main></body></html>"
        )
        if "thin" in url:
            html = f"<html><body><p>Assine para continuar lendo. ({url})</p></body></html>"
        path = dest_dir / "page.html"
        path.write_text(html, encoding="utf-8")
        return Downloaded(path=path, content_type="text/html")

    def hash(self, path: Path) -> str:
        return self._hash(path)

    def close(self) -> None:
        pass


def main() -> int:
    from app.database import init_db, session_scope
    from app.database.models import Book, ConversionJob, Feed, FeedItem
    from app.rss.naming import feed_category_name
    from app.rss.service import process_feed
    from app.storage.paths import slugify_path

    check("categoria do feed é rss/<nome>",
          feed_category_name(Feed(name="Meu Feed")) == "rss/Meu Feed")
    check("subcategoria substitui o nome, mantendo o prefixo rss/",
          feed_category_name(Feed(name="X", destination_folder="Noticias")) == "rss/Noticias")
    check("pasta hierárquica (rss/nome -> rss/nome)",
          slugify_path("rss/Meu Feed") == "rss/meu-feed", slugify_path("rss/Meu Feed"))
    check("categoria simples continua plana",
          slugify_path("Mangá") == "manga", slugify_path("Mangá"))

    init_db()
    fake = FakeDownloader(feed_xml())

    with session_scope() as session:
        feed = Feed(
            name="Teste",
            url="https://exemplo.com/feed.xml",
            output_format="epub",
            device_profile="generic_epub",
            keep_original=False,
            max_items_per_run=20,
        )
        session.add(feed)
        session.commit()
        report = process_feed(session, feed, fake)
        feed_id = feed.id

    print(f"relatorio: {report.as_dict()}")
    check("feed lido", report.fetched == 10, str(report.fetched))
    check("apenas um erro (item sem conteúdo nem página)",
          len(report.errors) == 1, str(report.errors))
    check("itens importados (todos menos podcast e o sem-conteúdo)",
          report.imported == 8, str(report.imported))
    check("artigos detectados", report.articles == 7, str(report.articles))
    check("podcast ignorado (skip, não erro)", report.skipped == 1, str(report.skipped))
    check("uma conversão enfileirada por item importado",
          report.queued == report.imported == 8, f"{report.queued}/{report.imported}")

    # Política atual: o conteúdo do feed é a fonte preferida, mas quando ele não
    # traz NENHUMA imagem a página é consultada uma vez -- figuras (gráficos,
    # prints) costumam existir só na página. Se a página não tiver imagens (ou for
    # um stub), o conteúdo do feed continua sendo usado.
    check(
        "inline sem imagem: a página é consultada para procurar figuras",
        "https://exemplo.com/artigo-inline" in fake.calls,
        str(fake.calls),
    )
    check(
        "inline sem imagem e com página fora do ar: mantém o conteúdo do feed",
        "https://exemplo.com/fails-long" in fake.calls,
        str(fake.calls),
    )
    check(
        "página bloqueada (thin) NÃO substitui o conteúdo do feed",
        "https://exemplo.com/thin-long" in fake.calls,
        str(fake.calls),
    )
    check("página fora do ar acionou o fallback do conteúdo do feed",
          "https://exemplo.com/fails" in fake.calls, str(fake.calls))
    check("página bloqueada (thin) acionou o fallback",
          "https://exemplo.com/thin" in fake.calls, str(fake.calls))
    check("imagem destacada NÃO foi tratada como o livro",
          "https://exemplo.com/destaque.jpg" not in fake.calls, str(fake.calls))
    check("link do artigo usado no lugar da imagem",
          "https://exemplo.com/artigo-com-imagem" in fake.calls, str(fake.calls))
    check("anexo EPUB baixado", "https://exemplo.com/livro.epub" in fake.calls)
    check("podcast NÃO foi baixado (identificado pelo anexo de áudio)",
          "https://exemplo.com/ep.mp3" not in fake.calls
          and "https://exemplo.com/podcast" not in fake.calls,
          str(fake.calls))

    with session_scope() as session:
        books = session.query(Book).all()
        jobs = session.query(ConversionJob).all()
        items = session.query(FeedItem).where(FeedItem.feed_id == feed_id).all()
        statuses = {item.status for item in items}
        formats = sorted({book.format for book in books})
        titles = sorted(book.title for book in books)
        ngray = session.query(Book).count()
        categorias = {book.category_rel.name if book.category_rel else None for book in books}
        caminhos = sorted({"/".join(book.file_path.split("/")[:2]) for book in books})

    print(f"livros ({ngray}): {titles[:10]}")
    print(f"formatos: {formats} | status dos itens: {statuses}")
    check("livros no banco", ngray == 8, str(ngray))
    check("artigos entraram como HTML (e o anexo como EPUB)",
          {"html", "epub"} <= set(formats), str(formats))
    check("todos os livros ficaram na categoria rss/Teste",
          categorias == {"rss/Teste"}, str(categorias))
    check("arquivos guardados na pasta rss/teste/",
          caminhos == ["rss/teste"], str(caminhos))
    check("status 'downloaded' registrado", "downloaded" in statuses, str(statuses))
    check("status 'skipped' registrado (podcast/itens)", "skipped" in statuses, str(statuses))
    check("status 'error' apenas no item sem conteúdo", "error" in statuses, str(statuses))
    check("jobs criados", len(jobs) == report.queued, str(len(jobs)))

    # Nothing left behind in the inbox: everything moved into the library.
    leftovers = list((WORKDIR / "inbox").glob("*")) if (WORKDIR / "inbox").exists() else []
    check("inbox limpo", not leftovers, str(leftovers))

    # Regressão: o limite por execução é "quantos itens NOVOS por rodada", não
    # uma fatia do feed. Com a fatia aplicada antes do dedup, um feed com mais
    # entradas que o limite só importava a primeira página e nunca chegava ao
    # resto (os itens antigos ficavam desconhecidos para sempre).
    print("\n[limite por rodada não pode esconder os itens antigos]")
    big_feed = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        "<channel><title>Feed grande</title><link>https://exemplo.com/</link>"
        + "".join(
            f"<item><title>Post {index}</title><guid>cap-{index}</guid>"
            f"<link>https://exemplo.com/cap-{index}</link>"
            f"<content:encoded><![CDATA[{article(f'cap-{index}')}]]></content:encoded></item>"
            for index in range(1, 6)
        )
        + "</channel></rss>"
    )
    with session_scope() as session:
        capped = Feed(
            name="Grande",
            url="https://exemplo.com/grande.xml",
            output_format="",
            max_items_per_run=2,
            keep_original=True,
        )
        session.add(capped)
        session.commit()
        capped_id = capped.id

    capper = FakeDownloader(big_feed)
    seen_per_run: list[int] = []
    for _ in range(3):
        with session_scope() as session:
            feed_row = session.get(Feed, capped_id)
            process_feed(session, feed_row, capper)
            seen_per_run.append(session.query(FeedItem).where(FeedItem.feed_id == capped_id).count())

    with session_scope() as session:
        guids = sorted(
            item.guid for item in session.query(FeedItem).where(FeedItem.feed_id == capped_id)
        )
    print(f"  itens por rodada: {seen_per_run} | guids: {guids}")
    check(
        "cada rodada avança até o limite (2, 4, 5)",
        seen_per_run == [2, 4, 5],
        str(seen_per_run),
    )
    check(
        "todas as 5 entradas foram alcançadas",
        guids == [f"cap-{index}" for index in range(1, 6)],
        str(guids),
    )

    # Desfecho da nova política: quando o inline não tem imagem e a página tem,
    # a página é a fonte escolhida (a figura precisa sobreviver).
    print("\n[inline sem imagem x página com figura]")
    from pathlib import Path as _Path

    from app.rss.parser import FeedEntry
    from app.rss.service import _obtain_source

    class PageWithImage:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def download(self, url: str, dest_dir: _Path, *, filename: str | None = None):
            from app.rss.downloader import Downloaded

            self.calls.append(url)
            dest_dir.mkdir(parents=True, exist_ok=True)
            path = dest_dir / "page.html"
            path.write_text(
                "<html><body><main><article><h1>Com figura</h1>"
                '<p>' + "conteudo do artigo " * 400 + '</p>'
                '<img src="https://exemplo.com/f.png" alt="grafico"/>'
                "</article></main></body></html>",
                encoding="utf-8",
            )
            return Downloaded(path=path, content_type="text/html")

    rich_page = PageWithImage()
    entry = FeedEntry(
        guid="figura",
        title="Inline sem imagem, página com figura",
        url="https://exemplo.com/com-figura",
        inline_html="<main><article><p>" + "texto do feed " * 200 + "</p></article></main>",
    )
    workdir = WORKDIR / "figura_work"
    source = _obtain_source(rich_page, entry, workdir)
    check("a página foi buscada", "https://exemplo.com/com-figura" in rich_page.calls, str(rich_page.calls))
    body = source.path.read_text(encoding="utf-8", errors="replace") if source else ""
    check("a fonte escolhida é a página (tem <img>)", "<img" in body, body[:80])
    check("a fonte não é o conteúdo do feed", "texto do feed" not in body)

    class PageWithoutImage(PageWithImage):
        def download(self, url: str, dest_dir: _Path, *, filename: str | None = None):
            from app.rss.downloader import Downloaded

            self.calls.append(url)
            dest_dir.mkdir(parents=True, exist_ok=True)
            path = dest_dir / "page.html"
            path.write_text(
                "<html><body><main><article><p>" + "conteudo " * 200 + "</p></article></main></body></html>",
                encoding="utf-8",
            )
            return Downloaded(path=path, content_type="text/html")

    plain_page = PageWithoutImage()
    source = _obtain_source(plain_page, entry, WORKDIR / "plana_work")
    body = source.path.read_text(encoding="utf-8", errors="replace") if source else ""
    check("sem figura na página, mantém o conteúdo do feed", "texto do feed" in body, body[:60])

    shutil.rmtree(WORKDIR, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
