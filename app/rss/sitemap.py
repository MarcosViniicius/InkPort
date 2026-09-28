"""Site archives: reading a sitemap and turning its posts into books.

RSS only carries what the site decides to publish (often the last 10-20 posts).
A site's ``sitemap.xml`` lists everything, so this module enumerates the archive
and the importer walks it year by year, reusing the normal download + import +
conversion pipeline.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
#: Post URLs look like ``/2026/09/28/some-slug/`` (Hugo, WordPress, Jekyll...).
_POST_RE = re.compile(r"/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/(?P<slug>[^/?#]+)")


@dataclass(slots=True)
class SitemapPost:
    url: str
    title: str
    year: int
    month: int = 0
    day: int = 0

    @property
    def published(self) -> str:
        """``YYYY-MM-DD`` when the URL carries the date, else just the year."""
        if self.month and self.day:
            return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"
        return f"{self.year:04d}"


def parse_sitemap(content: bytes, *, base_url: str = "") -> tuple[list[SitemapPost], list[str]]:
    """Return ``(posts, child_sitemaps)`` from a urlset or a sitemap index."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        logger.info("sitemap unreadable", extra={"error": str(exc)})
        return [], []

    posts: list[SitemapPost] = []
    children: list[str] = []
    for element in root.iter(f"{_NS}loc"):
        loc = (element.text or "").strip()
        if not loc:
            continue
        if loc.endswith(".xml"):
            children.append(loc)
            continue
        post = post_from_url(loc)
        if post is not None:
            posts.append(post)
    if not posts and not children:  # no namespace declared
        for element in root.iter("loc"):
            loc = (element.text or "").strip()
            if not loc:
                continue
            if loc.endswith(".xml"):
                children.append(loc)
                continue
            post = post_from_url(loc)
            if post is not None:
                posts.append(post)
    return posts, children


def post_from_url(url: str) -> SitemapPost | None:
    """A post when the URL looks like an article, otherwise ``None``."""
    match = _POST_RE.search(url)
    if match is None:
        return None
    slug = match.group("slug")
    # Index pages such as /2026/ or /2026/09/ are not posts.
    if slug in {"page", "index"} or slug.isdigit():
        return None
    return SitemapPost(
        url=url,
        title=title_from_slug(slug),
        year=int(match.group("year")),
        month=int(match.group("month")),
        day=int(match.group("day")),
    )


def title_from_slug(slug: str) -> str:
    """A readable title from a URL slug (the page's own title wins later)."""
    text = re.sub(r"[-_]+", " ", slug).strip()
    return text[:120] or slug


def posts_for_year(posts: list[SitemapPost], year: int | None) -> list[SitemapPost]:
    if year is None:
        return posts
    return [post for post in posts if post.year == year]


def current_year() -> int:
    return date.today().year


def collect_posts(
    fetch,
    sitemap_url: str,
    *,
    year: int | None = None,
    max_sitemaps: int = 20,
) -> list[SitemapPost]:
    """Walk a sitemap (and its children) and return the posts of ``year``.

    ``fetch(url) -> bytes`` is injected so this stays testable without network.
    """
    seen_maps = 0
    queue = [sitemap_url]
    visited: set[str] = set()
    posts: list[SitemapPost] = []

    while queue and seen_maps < max_sitemaps:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            content = fetch(url)
        except Exception as exc:  # noqa: BLE001 - one broken sitemap is not fatal
            logger.warning("sitemap fetch failed", extra={"url": url, "error": str(exc)})
            continue
        seen_maps += 1
        found, children = parse_sitemap(content, base_url=url)
        posts.extend(posts_for_year(found, year))
        queue.extend(children)

    # Oldest first: a blog is nicer to read in chronological order.
    posts.sort(key=lambda post: (post.year, post.month, post.day, post.url))
    return posts
