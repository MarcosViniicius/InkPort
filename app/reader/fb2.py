"""FictionBook 2 (FB2) handler.

FB2 is XML, so it is parsed safely (entities disabled, no network) and turned
into HTML: sections become chapters and styles (emphasis, epigraphs, poems,
citations) are preserved. Embedded ``<binary>`` images are served through the
asset route.
"""

from __future__ import annotations

import base64
from html import escape
from urllib.parse import unquote

from lxml import etree
from starlette.responses import HTMLResponse, Response

from app.reader.base import ReaderContext, ReaderError, ReaderHandler
from app.reader.sanitize import build_document

_XLINK = "http://www.w3.org/1999/xlink"
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, recover=True, huge_tree=False)

_BLOCK_TAGS = {
    "p": "p",
    "subtitle": "h2",
    "empty-line": "br",
    "epigraph": "blockquote",
    "cite": "blockquote",
    "poem": "div",
    "stanza": "div",
    "v": "p",
    "text-author": "p",
    "annotation": "div",
    "table": "table",
    "tr": "tr",
    "td": "td",
    "th": "th",
}
_INLINE_TAGS = {
    "emphasis": "em",
    "strong": "strong",
    "strikethrough": "s",
    "sub": "sub",
    "sup": "sup",
    "code": "code",
}


class Fb2Handler(ReaderHandler):
    name = "fb2"
    label = "FB2"
    formats = frozenset({"fb2"})

    def capabilities(self, ctx: ReaderContext) -> dict:
        return {"native": False, "pages": False, "chapters": True, "assets": True, "audio": False}

    def manifest(self, ctx: ReaderContext) -> dict:
        from app.reader.manifest import build_manifest

        root = self._root(ctx)
        sections = _sections(root)
        chapters = [
            {"key": str(index), "title": _section_title(section) or f"Parte {index + 1}",
             "index": index}
            for index, section in enumerate(sections)
        ]
        return build_manifest(
            ctx, handler=self, capabilities=self.capabilities(ctx), chapters=chapters
        )

    def chapter(self, ctx: ReaderContext, key: str) -> HTMLResponse:
        root = self._root(ctx)
        sections = _sections(root)
        try:
            index = int(key)
            section = sections[index]
        except (ValueError, IndexError) as exc:
            raise ReaderError("Capítulo não encontrado.", status_code=404) from exc
        title = _section_title(section) or f"Parte {index + 1}"
        body = _render_children(section, ctx.book_id)
        html = build_document(
            f"<article class=\"reader-fb2\"><h1>{escape(title)}</h1>{body}</article>",
            title=title,
            extra_head="<style>body{margin:0;padding:1.6em;}"
            ".reader-fb2 blockquote{margin:1em 1.4em;font-style:italic;}"
            ".reader-fb2 .fb2-v{margin:.2em 0;}"
            ".reader-fb2 .fb2-author{text-align:right;font-style:italic;}"
            ".reader-fb2 img{max-width:100%;height:auto;}</style>",
        )
        return HTMLResponse(html, headers={"Cache-Control": "private, max-age=3600"})

    def asset(self, ctx: ReaderContext, key: str) -> Response:
        internal = unquote(key)
        if not internal.startswith("binary/"):
            raise ReaderError("Recurso não encontrado.", status_code=404)
        target = internal[len("binary/"):]
        for element in self._root(ctx).iter():
            if isinstance(element.tag, str) and element.tag.endswith("binary") \
                    and element.attrib.get("id") == target:
                try:
                    data = base64.b64decode("".join(element.itertext()), validate=False)
                except (ValueError, TypeError) as exc:
                    raise ReaderError("Imagem embutida inválida.") from exc
                mime = element.attrib.get("content-type") or "application/octet-stream"
                return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})
        raise ReaderError("Imagem não encontrada.", status_code=404)

    def _root(self, ctx: ReaderContext):
        try:
            return etree.fromstring(ctx.path.read_bytes(), parser=_PARSER)
        except etree.XMLSyntaxError as exc:
            raise ReaderError("FB2 inválido: não foi possível ler o XML.") from exc


def _sections(root) -> list:
    bodies = [element for element in root if _tag(element) == "body"]
    sections: list = []
    for body in bodies:
        for child in body:
            if _tag(child) == "section":
                sections.append(child)
    if sections:
        return sections
    return [element for element in root if _tag(element) == "body"]


def _section_title(section) -> str:
    for child in section:
        if _tag(child) == "title":
            return " ".join("".join(child.itertext()).split())
    return ""


def _tag(element) -> str:
    return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""


def _render_children(element, book_id: str) -> str:
    return "".join(_render_node(child, book_id) for child in element)


def _render_node(node, book_id: str) -> str:
    tag = _tag(node)
    if not tag:
        return ""

    if tag == "title":
        text = " ".join("".join(node.itertext()).split())
        return f"<h2>{escape(text)}</h2>" if text else ""
    if tag == "image":
        href = node.attrib.get(f"{{{_XLINK}}}href") or node.attrib.get("href") or ""
        if not href.startswith("#"):
            return ""
        image_id = href[1:]
        return f'<img src="/reader/{book_id}/asset/binary/{escape(image_id)}" alt="">'
    if tag == "a":
        href = node.attrib.get(f"{{{_XLINK}}}href") or node.attrib.get("href") or ""
        text = escape("".join(node.itertext()))
        if href.startswith("#"):
            return f'<a href="{escape(href)}">{text}</a>'
        return text
    if tag == "section":
        return f"<section>{_render_children(node, book_id)}</section>"

    inner = _render_children(node, book_id)
    if tag in _INLINE_TAGS:
        return f"<{_INLINE_TAGS[tag]}>{inner or escape(_node_text(node))}</{_INLINE_TAGS[tag]}>"
    if tag in _BLOCK_TAGS:
        name = _BLOCK_TAGS[tag]
        if name == "br":
            return "<br>"
        css = _classes_for(tag)
        return f'<{name}{css}>{inner}</{name}>'
    if tag == "binary":
        return ""
    return inner


def _node_text(node) -> str:
    return escape("".join(node.itertext()))


def _classes_for(tag: str) -> str:
    if tag == "v":
        return ' class="fb2-v"'
    if tag == "text-author":
        return ' class="fb2-author"'
    if tag == "epigraph":
        return ' class="fb2-epigraph"'
    if tag == "poem":
        return ' class="fb2-poem"'
    if tag == "stanza":
        return ' class="fb2-stanza"'
    return ""
