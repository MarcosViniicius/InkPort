"""Build an EPUB from text chapters (used for HTML/articles)."""

from __future__ import annotations

import html
import posixpath
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from app.converters.epub.model import CONTAINER_XML, MIMETYPE, EpubMeta

STYLES_CSS = """html, body { margin: 0; padding: 0; }
body { font-family: serif; line-height: 1.5; margin: 5% 6%; }
h1 { font-size: 1.5em; line-height: 1.25; margin: 0 0 0.8em; }
h2 { font-size: 1.25em; margin: 1.6em 0 0.5em; }
h3, h4, h5, h6 { font-size: 1.1em; margin: 1.4em 0 0.4em; }
p { margin: 0 0 0.9em; text-align: justify; }
p.item { margin-left: 1.2em; }
blockquote { margin: 1em 1.5em; font-style: italic; }
pre { white-space: pre-wrap; background: #f4f4f4; padding: 0.6em; }
img { max-width: 100%; }
"""


def build_chapter(title: str, body_xhtml: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="pt">\n'
        f"<head><title>{html.escape(title)}</title>\n"
        '<meta charset="utf-8"/>\n'
        '<link rel="stylesheet" type="text/css" href="../styles.css"/>\n'
        "</head>\n"
        f'<body><h1>{html.escape(title)}</h1>\n{body_xhtml}\n</body></html>\n'
    )


def _fix_image_sources(body: str, images: list[tuple[str, bytes, str]]) -> str:
    """Point a chapter's images at ``../images/...``.

    Chapters live in ``OEBPS/text/`` and images in ``OEBPS/images/``, so a bare
    ``src="images/x.jpg"`` (what the pipelines produce) resolves to
    ``OEBPS/text/images/x.jpg`` -- a broken image in every reader.
    """
    for name, _data, _mime in images:
        base = posixpath.basename(name)
        for candidate in (f'src="images/{name}"', f'src="images/{base}"', f'src="{base}"'):
            body = body.replace(candidate, f'src="../images/{name}"')
    return body


def write_text_epub(
    out_path: Path,
    *,
    meta: EpubMeta,
    chapters: list[tuple[str, str]],
    images: list[tuple[str, bytes, str]] | None = None,
    cover: tuple[str, bytes, str] | None = None,
) -> Path:
    """Write an EPUB whose spine is a list of ``(title, body_xhtml)`` chapters.

    ``images`` are ``(filename, data, mime)`` resources referenced by the bodies.
    ``cover`` is ``(filename, data, mime)``: it becomes the first page and the
    book's cover, so readers show a thumbnail even before downloading the file.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    images = images or []
    if not chapters:
        chapters = [(meta.title, "<p></p>")]

    chapter_files = [f"text/chapter_{index:04d}.xhtml" for index in range(1, len(chapters) + 1)]
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/styles.css", STYLES_CSS)
        if cover is not None:
            name, data, _mime = cover
            zf.writestr(f"OEBPS/images/{name}", data, compress_type=zipfile.ZIP_STORED)
            zf.writestr("OEBPS/text/cover.xhtml", _cover_xhtml(name, meta.title), zipfile.ZIP_DEFLATED)
        for href, (title, body) in zip(chapter_files, chapters, strict=False):
            zf.writestr(f"OEBPS/{href}", build_chapter(title, _fix_image_sources(body, images)))
        for name, data, _mime in images:
            zf.writestr(f"OEBPS/images/{name}", data, compress_type=zipfile.ZIP_STORED)

        entries = [(title, href) for (title, _body), href in zip(chapters, chapter_files, strict=False)]
        zf.writestr("OEBPS/nav.xhtml", _nav_xhtml(entries))
        zf.writestr("OEBPS/toc.ncx", _ncx(meta, entries))
        zf.writestr("OEBPS/content.opf", _opf(meta, chapter_files, images, now, cover))

    return out_path


def read_cover(path: str | Path | None) -> tuple[str, bytes, str] | None:
    """``(filename, data, mime)`` for a cover image on disk, if it is usable."""
    if not path:
        return None
    candidate = Path(path)
    try:
        data = candidate.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    suffix = candidate.suffix.lower().lstrip(".") or "jpg"
    mime = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(suffix, "image/jpeg")
    if suffix not in {"jpg", "jpeg", "png", "gif", "webp"}:
        suffix = "jpg"
    return f"cover.{suffix}", data, mime


def _cover_xhtml(name: str, title: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="pt">\n'
        f"<head><title>{html.escape(title)}</title><meta charset=\"utf-8\"/>\n"
        "<style>html,body{margin:0;padding:0;height:100%;}"
        "body{display:flex;align-items:center;justify-content:center;background:#000;}"
        "img{max-width:100%;max-height:100%;}</style></head>\n"
        f'<body><img src="../images/{html.escape(name)}" alt="{html.escape(title)}"/></body></html>\n'
    )


def _opf(
    meta: EpubMeta,
    chapter_files: list[str],
    images: list[tuple[str, bytes, str]],
    modified: str,
    cover: tuple[str, bytes, str] | None = None,
) -> str:
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="styles.css" media-type="text/css"/>',
    ]
    if cover is not None:
        name, _data, mime = cover
        manifest.append(
            f'<item id="cover-img" href="images/{html.escape(name)}" '
            f'media-type="{html.escape(mime)}" properties="cover-image"/>'
        )
        manifest.append(
            '<item id="coverpage" href="text/cover.xhtml" media-type="application/xhtml+xml"/>'
        )
    manifest.extend(
        f'<item id="c{i}" href="{href}" media-type="application/xhtml+xml"/>'
        for i, href in enumerate(chapter_files)
    )
    manifest.extend(
        f'<item id="img{i}" href="images/{name}" media-type="{html.escape(mime)}"/>'
        for i, (name, _data, mime) in enumerate(images)
    )
    spine_items = [f'    <itemref idref="c{i}"/>' for i in range(len(chapter_files))]
    if cover is not None:
        spine_items.insert(0, '    <itemref idref="coverpage" linear="yes"/>')
    spine = "\n".join(spine_items)
    cover_meta = (
        '    <meta name="cover" content="cover-img"/>\n' if cover is not None else ""
    )
    guide = (
        '  <guide><reference type="cover" title="Capa" href="text/cover.xhtml"/></guide>\n'
        if cover is not None
        else ""
    )

    author = (
        f'    <dc:creator id="author">{html.escape(meta.author)}</dc:creator>\n'
        '    <meta refines="#author" property="role" scheme="marc:relators">aut</meta>\n'
        if meta.author
        else ""
    )
    description = (
        f"    <dc:description>{html.escape(meta.description)}</dc:description>\n"
        if meta.description
        else ""
    )
    publisher = (
        f"    <dc:publisher>{html.escape(meta.publisher)}</dc:publisher>\n"
        if meta.publisher
        else ""
    )
    date = f"    <dc:date>{html.escape(meta.date)}</dc:date>\n" if meta.date else ""
    subjects = "".join(
        f"    <dc:subject>{html.escape(subject)}</dc:subject>\n"
        for subject in meta.subjects
    )
    series = ""
    if meta.series:
        series = f'    <meta name="calibre:series" content="{html.escape(meta.series)}"/>\n'

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid" xml:lang="' + html.escape(meta.language) + '">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:opf="http://www.idpf.org/2007/opf">\n'
        f'    <dc:identifier id="bookid">{html.escape(meta.identifier)}</dc:identifier>\n'
        f"    <dc:title>{html.escape(meta.title)}</dc:title>\n"
        f"    <dc:language>{html.escape(meta.language)}</dc:language>\n"
        f"{author}{publisher}{description}{date}{subjects}{series}"
        f"{cover_meta}"
        f'    <meta property="dcterms:modified">{modified}</meta>\n'
        f'    <meta name="generator" content="{html.escape(meta.generator)}"/>\n'
        "  </metadata>\n"
        "  <manifest>\n    " + "\n    ".join(manifest) + "\n  </manifest>\n"
        f'  <spine toc="ncx">\n{spine}\n  </spine>\n'
        f"{guide}"
        "</package>\n"
    )


def _nav_xhtml(entries: list[tuple[str, str]]) -> str:
    items = "\n".join(
        f'      <li><a href="{html.escape(href)}">{html.escape(title)}</a></li>'
        for title, href in entries
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="pt">\n'
        '<head><title>Sumário</title><meta charset="utf-8"/></head>\n'
        '<body><nav epub:type="toc" id="toc"><h1>Sumário</h1><ol>\n'
        f"{items}\n</ol></nav></body></html>\n"
    )


def _ncx(meta: EpubMeta, entries: list[tuple[str, str]]) -> str:
    points = "\n".join(
        f'    <navPoint id="navPoint-{i}" playOrder="{i}">'
        f"<navLabel><text>{html.escape(title)}</text></navLabel>"
        f'<content src="{html.escape(href)}"/></navPoint>'
        for i, (title, href) in enumerate(entries, start=1)
    )
    identifier = html.escape(meta.identifier or f"urn:uuid:{uuid.uuid4()}")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        "  <head>\n"
        f'    <meta name="dtb:uid" content="{identifier}"/>\n'
        '    <meta name="dtb:depth" content="1"/>\n'
        "  </head>\n"
        f"  <docTitle><text>{html.escape(meta.title)}</text></docTitle>\n"
        f"  <navMap>\n{points}\n  </navMap>\n</ncx>\n"
    )
