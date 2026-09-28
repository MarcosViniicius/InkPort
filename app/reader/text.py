"""Plain-text handler.

Text has no structure of its own, so we detect a few obvious chapter headings
(``CAPÍTULO X``, ``Chapter N``, ``PARTE``, ``LIVRO``) and split the file into
navigable chapters, then render them with the reader typography.
"""

from __future__ import annotations

import re
from html import escape

from starlette.responses import HTMLResponse

from app.reader.base import ReaderContext, ReaderError, ReaderHandler
from app.reader.sanitize import build_document

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

_HEADING = re.compile(
    r"^\s*(cap[ií]tulo|chapter|parte|part|section|se[çc][ãa]o|livro|book|prologue|prólogo)\b.*$",
    re.IGNORECASE,
)
_ALL_CAPS = re.compile(r"^[A-ZÀÁÂÃÄÉÊÍÓÔÕÖÚÜÇ0-9][A-ZÀ-Ü0-9 \-\'\.,:;!?]{2,58}$")


class TextHandler(ReaderHandler):
    name = "text"
    label = "Texto"
    formats = frozenset({"txt"})

    def capabilities(self, ctx: ReaderContext) -> dict:
        return {"native": False, "pages": False, "chapters": True, "assets": False, "audio": False}

    def manifest(self, ctx: ReaderContext) -> dict:
        from app.reader.manifest import build_manifest

        text = self._text(ctx)
        sections = _split_chapters(text)
        chapters = [
            {"key": str(index), "title": section["title"], "index": index}
            for index, section in enumerate(sections)
        ]
        return build_manifest(
            ctx, handler=self, capabilities=self.capabilities(ctx), chapters=chapters
        )

    def chapter(self, ctx: ReaderContext, key: str) -> HTMLResponse:
        text = self._text(ctx)
        sections = _split_chapters(text)
        try:
            index = int(key)
            section = sections[index]
        except (ValueError, IndexError) as exc:
            raise ReaderError("Capítulo não encontrado.", status_code=404) from exc
        return HTMLResponse(_render(section["title"], section["body"]), headers=_HEADERS)

    def _text(self, ctx: ReaderContext) -> str:
        data = ctx.path.read_bytes()
        for encoding in _ENCODINGS:
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 70:
        return False
    if _HEADING.match(stripped):
        return True
    return bool(_ALL_CAPS.match(stripped))


def _split_chapters(text: str) -> list[dict]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections: list[dict] = []
    current_title = "Início"
    current: list[str] = []

    def flush() -> None:
        body = "\n".join(current).strip("\n")
        if body.strip():
            sections.append({"title": current_title, "body": body})
        current.clear()

    for line in lines:
        if _is_heading(line):
            flush()
            current_title = line.strip()
        else:
            current.append(line)
    flush()

    if not sections:
        body = text.strip()
        sections = [{"title": "Texto", "body": body}] if body else []
    return sections


def _render(title: str, body: str) -> str:
    paragraphs: list[str] = []
    for block in re.split(r"\n{2,}", body):
        block = block.strip("\n")
        if not block.strip():
            continue
        paragraphs.append(f"<p>{escape(block)}</p>")
    inner = "\n".join(paragraphs) or f"<p>{escape(body)}</p>"
    return build_document(
        f"<article class=\"reader-txt\"><h1 class=\"reader-txt-title\">{escape(title)}</h1>"
        f"<div class=\"reader-txt-body\">{inner}</div></article>",
        title=title,
        extra_head="<style>body{margin:0;padding:1.6em;}"
        ".reader-txt-title{font-size:1.4em;margin:0 0 1em;}"
        ".reader-txt-body p{margin:0 0 1em;text-indent:0;}</style>",
    )


_HEADERS = {"Cache-Control": "private, max-age=3600"}
