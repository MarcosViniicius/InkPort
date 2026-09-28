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
from app.database.models import Book, Feed, SourceKind
from app.library.conversions import enqueue_conversion
from app.library.importer import import_file
from app.rss.downloader import Downloader, DownloadError
from app.rss.naming import feed_category_name
from app.rss.sitemap import SitemapPost, collect_posts, current_year
from app.storage.temp import temp_workdir

logger = logging.getLogger(__name__)


def known_urls(session) -> set[str]:
    """URLs the library already holds, by source URL or source id.

    Only *books* count: a feed item whose file was deleted (or whose conversion
    replaced it) must not block a re-import, otherwise the archive could never be
    pulled again.
    """
    from sqlalchemy import select

    urls: set[str] = set()
    for column in (Book.source_url, Book.source_id):
        for value in session.scalars(select(column).where(column.is_not(None))):
            urls.add(_normalise(value))
    urls.discard("")
    return urls


def _normalise(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().removeprefix("www.")
    path = (parsed.path or "/").rstrip("/")
    return f"{host}{path}"


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


def import_posts(
    posts: list[SitemapPost],
    *,
    category: str,
    output_format: str,
    device_profile: str,
    keep_original: bool,
    limit: int = 0,
    dry_run: bool = False,
    on_progress=None,
) -> dict:
    """Download, import and queue the conversion of each post."""
    stats = {"considered": len(posts), "imported": 0, "queued": 0, "skipped": 0, "errors": 0}

    with session_scope() as session:
        known = known_urls(session)

    todo = [post for post in posts if _normalise(post.url) not in known]
    stats["skipped"] = len(posts) - len(todo)
    if limit:
        todo = todo[:limit]
    stats["todo"] = len(todo)
    if dry_run:
        return stats

    downloader = Downloader()
    try:
        for index, post in enumerate(todo, start=1):
            if on_progress:
                on_progress(index, len(todo), post)
            try:
                with temp_workdir("sitemap_") as workdir:
                    downloaded = downloader.download(post.url, workdir)
                    with session_scope() as session:
                        outcome = import_file(
                            session,
                            downloaded.path,
                            category=category,
                            source=SourceKind.IMPORT.value,
                            source_url=post.url,
                            source_id=post.url,
                            move=True,
                            title_override=post.title,
                        )
                        if outcome.status != "imported" or outcome.book is None:
                            stats["errors"] += 1
                            logger.info(
                                "sitemap post not imported",
                                extra={"url": post.url, "status": outcome.status},
                            )
                            continue
                        book = outcome.book
                        book.published = post.published
                        if output_format:
                            enqueue_conversion(
                                session,
                                book,
                                target_format=output_format,
                                device_profile=device_profile,
                                keep_original=keep_original,
                            )
                            stats["queued"] += 1
                        stats["imported"] += 1
                        logger.info(
                            "sitemap post imported",
                            extra={"url": post.url, "title": book.title, "year": post.year},
                        )
            except DownloadError as exc:
                stats["errors"] += 1
                logger.warning("sitemap download failed", extra={"url": post.url, "error": str(exc)})
            except Exception:  # noqa: BLE001 - one post must not stop the archive
                stats["errors"] += 1
                logger.exception("sitemap post failed", extra={"url": post.url})
    finally:
        downloader.close()
    return stats


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
        novos = [post for post in posts if _normalise(post.url) not in known]
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

    stats = import_posts(
        posts,
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
