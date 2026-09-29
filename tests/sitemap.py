"""Importação de acervo por sitemap (o que o RSS não carrega).

Cobre: leitura de sitemap index e urlset, filtro por ano, ordem cronológica,
título a partir do slug, herança das configurações do feed do mesmo site e
dedup por URL (com www/barra final normalizados).

Run with:  python tests/sitemap.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="opds_sitemap_"))
os.environ["DATA_DIR"] = str(WORKDIR)
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ.setdefault("ADMIN_PASSWORD", "test-password")  # senha explicita: pula o assistente

PASSED: list[str] = []
FAILED: list[str] = []

INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://exemplo.com/pt/sitemap.xml</loc></sitemap>
  <sitemap><loc>https://exemplo.com/en/sitemap.xml</loc></sitemap>
</sitemapindex>"""

URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://exemplo.com/2026/09/28/post-novo/</loc></url>
  <url><loc>https://exemplo.com/2026/01/02/outro-post/</loc></url>
  <url><loc>https://exemplo.com/2025/12/31/post-antigo/</loc></url>
  <url><loc>https://exemplo.com/2026/</loc></url>
  <url><loc>https://exemplo.com/2026/01/</loc></url>
  <url><loc>https://exemplo.com/tags/llms/</loc></url>
  <url><loc>https://exemplo.com/sobre/</loc></url>
</urlset>"""

NO_NAMESPACE = b"""<?xml version="1.0"?>
<urlset><url><loc>https://exemplo.com/2024/03/04/sem-namespace/</loc></url></urlset>"""


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def main() -> int:
    from app.database import init_db, session_scope
    from app.database.models import Book, Feed
    from app.rss.sitemap import (
        collect_posts,
        current_year,
        parse_sitemap,
        post_from_url,
        posts_for_year,
    )
    from app.tools.import_site import _normalise, feed_defaults, import_posts, known_urls

    print("[leitura do sitemap]")
    posts, children = parse_sitemap(INDEX)
    check("sitemap index devolve os filhos", len(children) == 2, str(children))
    check("sitemap index não tem posts", posts == [], str(posts))

    posts, _ = parse_sitemap(URLSET)
    check("urlset devolve 3 posts", len(posts) == 3, str([p.url for p in posts]))
    check(
        "páginas de índice (/2026/, /2026/01/) são ignoradas",
        all(post.day for post in posts),
        str([post.url for post in posts]),
    )
    check("tags e páginas soltas não viram post", post_from_url("https://exemplo.com/tags/x/") is None)
    check("url com data vira post", post_from_url("https://exemplo.com/2026/09/28/x/") is not None)
    check(
        "título legível a partir do slug",
        posts[0].title == "post novo",
        posts[0].title,
    )
    check(
        "sem namespace também funciona",
        len(parse_sitemap(NO_NAMESPACE)[0]) == 1,
    )

    print("\n[filtro por ano e ordem]")
    ano_2026 = posts_for_year(posts, 2026)
    check("filtra só 2026", len(ano_2026) == 2, str([p.year for p in ano_2026]))
    check("data publicada completa", ano_2026[0].published == "2026-09-28", ano_2026[0].published)

    def fetch(url: str) -> bytes:
        return URLSET if "pt/sitemap" in url else INDEX

    coletados = collect_posts(fetch, "https://exemplo.com/sitemap.xml", year=2026)
    check("desce do índice até o urlset", len(coletados) == 2, str(len(coletados)))
    check(
        "ordem cronológica (mais antigo primeiro)",
        [p.published for p in coletados] == ["2026-01-02", "2026-09-28"],
        str([p.published for p in coletados]),
    )
    todos = collect_posts(fetch, "https://exemplo.com/sitemap.xml", year=None)
    check("sem filtro, todos os anos", len(todos) == 3, str(len(todos)))
    check("ano atual é plausível", current_year() >= 2024, str(current_year()))

    print("\n[sitemap filho quebrado não derruba a importação]")
    def partially_broken(url: str) -> bytes:
        if "en/sitemap" in url:
            raise OSError("sem conexão")
        return URLSET if "pt/sitemap" in url else INDEX

    survived = collect_posts(partially_broken, "https://exemplo.com/sitemap.xml", year=2026)
    check("segue com os sitemaps que funcionam", len(survived) == 2, str(len(survived)))

    print("\n[dedup por URL e herança do feed]")
    check(
        "www e barra final não criam duplicata",
        _normalise("https://www.exemplo.com/2026/09/28/post-novo/")
        == _normalise("https://exemplo.com/2026/09/28/post-novo"),
    )

    init_db()
    with session_scope() as session:
        session.add(
            Feed(
                name="exemplo",
                url="https://exemplo.com/index.xml",
                output_format="azw3",
                device_profile="kindle_paperwhite",
                keep_original=True,
            )
        )
        session.commit()

    with session_scope() as session:
        defaults = feed_defaults(session, "https://www.exemplo.com/pt/sitemap.xml")
    check("herda a categoria do feed do mesmo site", defaults["category"] == "rss/exemplo", str(defaults))
    check("herda formato e perfil", (defaults["output_format"], defaults["device_profile"]) == ("azw3", "kindle_paperwhite"), str(defaults))

    with session_scope() as session:
        session.add(
            Book(
                id="ja-tenho",
                title="Post novo",
                format="epub",
                content_type="ebook",
                media_type="application/epub+zip",
                file_path="x.epub",
                source="import",
                source_url="https://exemplo.com/2026/09/28/post-novo/",
            )
        )
        session.commit()

    with session_scope() as session:
        known = known_urls(session)
        stats = import_posts(
            coletados,
            category="rss/exemplo",
            output_format="",
            device_profile="eink_generic",
            keep_original=False,
            dry_run=True,
        )
        livros = session.query(Book).count()

    check("URL já importada entra em 'known'", _normalise("https://www.exemplo.com/2026/09/28/post-novo") in known)
    check("dry-run não cria livro", livros == 1, str(livros))
    check(
        "dry-run conta o que faria (1 novo de 2)",
        stats["todo"] == 1 and stats["skipped"] == 1,
        str(stats),
    )
    check("dry-run não baixa nada (imported=0)", stats["imported"] == 0, str(stats))

    shutil.rmtree(WORKDIR, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
