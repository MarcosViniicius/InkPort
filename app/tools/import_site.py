"""Import a site archive (sitemap) into the library.

Useful for blogs whose RSS only carries the last few posts: the sitemap lists
everything, so this walks it and imports the chosen year through the same
download + import + conversion pipeline the RSS worker uses.

    python -m app.tools.import_site https://example.com/sitemap.xml --year 2026
    python -m app.tools.import_site https://example.com/sitemap.xml --list
    python -m app.tools.import_site https://example.com/sitemap.xml --year 2026 --limit 10

Options worth knowing:

* ``--list`` only reports what would be imported (nothing is downloaded);
* ``--limit`` caps how many posts this run handles (the rest waits for the next);
* ``--category`` overrides the destination (default: the existing feed's
  category for that site, or ``site/<host>``);
* ``--format``/``--profile`` pick the conversion (default: the matching feed's
  settings, or EPUB for the generic profile).

Already imported URLs (matched on the book's source URL or the feed history) are
skipped, so it is safe to run again.
"""

from __future__ import annotations

import argparse
import logging
import sys
from urllib.parse import urlparse

from app.database import session_scope
from app.database.models import Feed
from app.rss.archive import import_posts, known_urls, normalise_url
from app.rss.downloader import Downloader
from app.rss.naming import feed_category_name
from app.rss.sitemap import SitemapPost, collect_posts, current_year

logger = logging.getLogger(__name__)


def feed_defaults(session, sitemap_url: str) -> dict:
    """Conversion settings taken from the feed of the same site, when there is one."""
    host = (urlparse(sitemap_url).netloc or "").lower().removeprefix("www.")
    from sqlalchemy import select

    for feed in session.scalars(select(Feed)):
        feed_host = (urlparse(feed.url).netloc or "").lower().removeprefix("www.")
        if feed_host == host:
            return {
                "category": feed_category_name(feed),
                "output_format": feed.output_format or "epub",
                "device_profile": feed.device_profile or "eink_generic",
                "keep_original": bool(feed.keep_original),
                "feed_name": feed.name,
            }
    return {
        "category": f"site/{host or 'sitemap'}",
        "output_format": "epub",
        "device_profile": "eink_generic",
        "keep_original": False,
        "feed_name": host or "sitemap",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importa posts de um sitemap para a biblioteca.")
    parser.add_argument("sitemap", help="URL do sitemap (ex.: https://site/sitemap.xml)")
    parser.add_argument("--year", type=int, default=None, help="ano a importar (padrão: ano atual)")
    parser.add_argument("--all-years", action="store_true", help="importa o acervo inteiro")
    parser.add_argument("--limit", type=int, default=0, help="máximo de posts nesta execução")
    parser.add_argument("--category", default="", help="categoria de destino")
    parser.add_argument("--format", default="", help="formato de saída (ex.: epub, azw3)")
    parser.add_argument("--profile", default="", help="perfil de dispositivo")
    parser.add_argument("--list", action="store_true", help="apenas lista, sem baixar nada")
    parser.add_argument("--keep-original", action="store_true", help="mantém o arquivo baixado")
    args = parser.parse_args(argv)

    from app.database import init_db

    init_db()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    year = None if args.all_years else (args.year or current_year())
    with Downloader() as downloader:
        posts = collect_posts(downloader.fetch, args.sitemap, year=year)

    print(f"sitemap: {args.sitemap}")
    print(f"ano: {'todos' if year is None else year} | posts encontrados: {len(posts)}")
    if args.list:
        with session_scope() as session:
            known = known_urls(session)
        novos = [post for post in posts if normalise_url(post.url) not in known]
        print(f"já na biblioteca: {len(posts) - len(novos)} | novos: {len(novos)}")
        for post in novos[:40]:
            print(f"  {post.published}  {post.title[:60]}")
        if len(novos) > 40:
            print(f"  ... e mais {len(novos) - 40}")
        return 0

    with session_scope() as session:
        defaults = feed_defaults(session, args.sitemap)
    category = args.category or defaults["category"]

    def progress(index: int, total: int, post: SitemapPost) -> None:
        print(f"  [{index}/{total}] {post.published} {post.title[:52]}")

    with Downloader() as downloader:
        stats = import_posts(
            posts,
            downloader=downloader,
            category=category,
            output_format=args.format or defaults["output_format"],
            device_profile=args.profile or defaults["device_profile"],
            keep_original=args.keep_original or defaults["keep_original"],
            limit=args.limit,
            dry_run=False,
            on_progress=progress,
        )
    print(
        f"\ncategoria: {category} | importados: {stats['imported']} | na fila: {stats['queued']}"
        f" | já existiam: {stats['skipped']} | erros: {stats['errors']}"
    )
    return 0 if not stats["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
