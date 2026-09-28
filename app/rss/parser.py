"""RSS/Atom/JSON feed parsing into a small, predictable entry structure.

Feeds differ a lot: some ship a downloadable file (enclosure), some only a link
to an article, and most include the article itself inline (``content`` or
``summary``). The entry keeps all of those so the worker can pick the best
source without another network round-trip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urljoin

import feedparser

logger = logging.getLogger(__name__)

#: Explicit downloadable files. When an entry offers one of these, it is a book,
#: not an article.
FILE_MIME_TYPES = (
    "application/epub+zip",
    "application/pdf",
    "application/x-mobipocket-ebook",
    "application/vnd.comicbook+zip",
    "application/vnd.comicbook-rar",
    "application/zip",
    "application/x-rar",
    "application/x-rar-compressed",
    "application/x-7z-compressed",
)
FILE_EXTENSIONS = (
    ".epub", ".pdf", ".mobi", ".azw", ".azw3", ".cbz", ".cbr", ".cb7",
    ".zip", ".rar", ".7z", ".fb2", ".txt",
)

#: Audio enclosures mean a podcast: there is nothing to read, so the worker
#: skips the item (and must not turn the episode page into a book).
AUDIO_MIME_PREFIX = "audio/"
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus", ".aac", ".wav")

#: Inline content below this length is just a teaser; the page is fetched then.
MIN_INLINE_HTML = 1000


@dataclass(slots=True)
class FeedEntry:
    guid: str
    title: str | None = None
    url: str | None = None
    summary: str | None = None
    published: str | None = None
    content_type: str | None = None
    extensions: list[str] = field(default_factory=list)
    #: Full article HTML carried by the feed itself, when available.
    inline_html: str | None = None

    @property
    def has_inline_article(self) -> bool:
        return bool(self.inline_html and len(self.inline_html) >= MIN_INLINE_HTML)

    @property
    def is_audio(self) -> bool:
        """Podcast episode: audio enclosure, nothing to read."""
        mime = (self.content_type or "").lower()
        if mime.startswith(AUDIO_MIME_PREFIX):
            return True
        return any(f".{ext}" in AUDIO_EXTENSIONS for ext in self.extensions)


@dataclass(slots=True)
class ParsedFeed:
    title: str | None
    entries: list[FeedEntry]
    error: str | None = None
    site_url: str | None = None


def parse_feed(content: bytes, base_url: str = "") -> ParsedFeed:
    parsed = feedparser.parse(content)
    if getattr(parsed, "bozo", 0) and not parsed.entries:
        return ParsedFeed(title=None, entries=[], error=_bozo_message(parsed))

    site_url = parsed.feed.get("link") or base_url
    entries: list[FeedEntry] = []
    for raw in parsed.entries:
        entry = _to_entry(raw, base_url)
        if entry is not None:
            entries.append(entry)

    return ParsedFeed(title=parsed.feed.get("title"), entries=entries, site_url=site_url)


def _to_entry(raw, base_url: str) -> FeedEntry | None:
    guid = raw.get("id") or raw.get("guid") or raw.get("link")
    inline = _inline_html(raw)
    if not guid:
        if inline:
            guid = _inline_identifier(raw, inline)
        else:
            return None

    url, content_type = _best_url(raw, base_url)

    return FeedEntry(
        guid=str(guid),
        title=(raw.get("title") or "").strip() or None,
        url=url,
        summary=raw.get("summary"),
        published=raw.get("published") or raw.get("updated"),
        content_type=content_type,
        extensions=_enclosure_extensions(raw),
        inline_html=inline,
    )


def _inline_html(raw) -> str | None:
    """The article HTML the feed itself carries, if it is substantial."""
    best = ""
    for block in raw.get("content", []) or []:
        value = block.get("value") if isinstance(block, dict) else None
        if value and len(value) > len(best):
            best = value

    summary = raw.get("summary") or ""
    if len(summary) > len(best):
        best = summary

    return best or None


def _inline_identifier(raw, inline: str) -> str:
    """Stable id for feeds whose entries have no guid/link at all."""
    from hashlib import sha1

    seed = (raw.get("title") or "") + inline[:500]
    return "urn:sha1:" + sha1(seed.encode("utf-8", "replace")).hexdigest()


def _best_url(raw, base_url: str) -> tuple[str | None, str | None]:
    """Pick the best download target.

    Order matters: an explicit book file beats the article link, and the article
    link beats a bare image (many feeds attach a featured image as an enclosure;
    downloading that instead of the article would be wrong).
    """
    for enclosure in raw.get("enclosures", []) or []:
        href = enclosure.get("href")
        mime = (enclosure.get("type") or "").lower()
        if href and (
            mime in FILE_MIME_TYPES
            or mime.startswith(AUDIO_MIME_PREFIX)
            or _looks_like_file(href)
            or _looks_like_audio(href)
        ):
            return urljoin(base_url, href), mime

    for link in raw.get("links", []) or []:
        href = link.get("href")
        mime = (link.get("type") or "").lower()
        if href and (
            mime in FILE_MIME_TYPES
            or mime.startswith(AUDIO_MIME_PREFIX)
            or _looks_like_file(href)
        ):
            return urljoin(base_url, href), mime

    link = raw.get("link")
    if link:
        return urljoin(base_url, link), None

    for enclosure in raw.get("enclosures", []) or []:
        href = enclosure.get("href")
        if href and (enclosure.get("type") or "").lower().startswith("image/"):
            return urljoin(base_url, href), enclosure.get("type")

    for enclosure in raw.get("enclosures", []) or []:
        if enclosure.get("href"):
            return urljoin(base_url, enclosure["href"]), enclosure.get("type")

    return None, None


def _looks_like_file(href: str) -> bool:
    path = href.split("?")[0].split("#")[0].lower()
    return any(path.endswith(ext) for ext in FILE_EXTENSIONS)


def _looks_like_audio(href: str) -> bool:
    path = href.split("?")[0].split("#")[0].lower()
    return any(path.endswith(ext) for ext in AUDIO_EXTENSIONS)


def _enclosure_extensions(raw) -> list[str]:
    exts: list[str] = []
    for enclosure in raw.get("enclosures", []) or []:
        href = enclosure.get("href") or ""
        if "." in href:
            exts.append(href.rsplit(".", 1)[-1].lower())
    return exts


def _bozo_message(parsed) -> str:
    exc = getattr(parsed, "bozo_exception", None)
    return f"Feed inválido: {exc}" if exc else "Feed inválido."
