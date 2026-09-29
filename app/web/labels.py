"""Human labels for technical identifiers.

The panel should never show a raw slug, a converter module name or a bare book
id. Everything user-facing goes through here.
"""

from __future__ import annotations

from app.database.models import Book

CONVERTER_LABELS = {
    "calibre": "Calibre (opcional)",
    "webpage_to_epub": "Conversor de páginas",
    "html_to_epub": "Conversor de páginas",
    "images_to_epub": "EPUB de imagens",
    "images_to_cbz": "CBZ",
    "images_to_pdf": "PDF",
    "epub_optimize": "Otimização de EPUB",
    "compress_image": "Compressão de imagem",
    # Conversores nativos (sem Calibre)
    "epub_to_kindle": "Escritor Kindle (AZW3/MOBI)",
    "epub_to_kepub": "KEPUB (Kobo)",
    "epub_to_pdf": "PDF a partir do EPUB",
    "epub_to_docx": "DOCX (Word)",
    "epub_to_fb2": "FB2 (FictionBook)",
    "epub_to_text": "Texto puro (TXT)",
    "pdf_to_epub": "Reflow do PDF",
    "text_to_epub": "Texto para EPUB",
    "fb2_to_epub": "FB2 para EPUB",
    "docx_to_epub": "DOCX para EPUB",
    "kindle_to_epub": "Kindle para EPUB",
}

FORMAT_LABELS = {
    "epub": "EPUB",
    "epub_optimized": "EPUB otimizado",
    "kepub": "KEPUB",
    "azw3": "AZW3",
    "mobi": "MOBI",
    "azw": "AZW",
    "prc": "PRC",
    "pdb": "PDB",
    "pdf": "PDF",
    "cbz": "CBZ",
    "cbr": "CBR",
    "cb7": "CB7",
    "cbt": "CBT",
    "docx": "DOCX",
    "rtf": "RTF",
    "fb2": "FB2",
    "txt": "TXT",
    "html": "HTML",
    "jpg": "JPG",
    "png": "PNG",
    "auto": "Automático",
}

CONTENT_LABELS = {
    "ebook": "Texto (reflow)",
    "comic": "Quadrinho / páginas de imagem",
    "document": "PDF",
    "image": "Imagem",
    "audio": "Áudio",
    "unknown": "Desconhecido",
}

SOURCE_LABELS = {
    "upload": "envio manual",
    "import": "importação",
    "rss": "feed",
    "download": "baixado da web",
}

#: Days per unit used by the retroactive period field.
BACKFILL_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}

STATUS_LABELS = {
    "pending": "na fila",
    "running": "executando",
    "done": "concluída",
    "failed": "falhou",
    "cancelled": "descartada",
    # feed items
    "seen": "visto",
    "downloaded": "baixado",
    "converted": "convertido",
    "skipped": "ignorado",
    "error": "erro",
    # import results
    "imported": "importado",
    "duplicate": "duplicado",
    "unsupported": "não suportado",
}

def converter_label(name: str | None) -> str:
    if not name:
        return "—"
    return CONVERTER_LABELS.get(name, name.replace("_", " "))


def format_label(fmt: str | None) -> str:
    if not fmt:
        return "—"
    key = fmt.lower()
    if key in FORMAT_LABELS:
        return FORMAT_LABELS[key]
    try:
        from app.converters.catalog import OUTPUT_LABELS

        if key in OUTPUT_LABELS:
            return OUTPUT_LABELS[key]
    except Exception:  # noqa: BLE001 - a label must never break a page
        pass
    return fmt.upper()


def status_label(status: str | None) -> str:
    if not status:
        return "—"
    return STATUS_LABELS.get(status, status)


def content_label(kind: str | None) -> str:
    if not kind:
        return "—"
    return CONTENT_LABELS.get(kind, kind)


def source_label(source: str | None) -> str:
    if not source:
        return "—"
    return SOURCE_LABELS.get(source, source)


def backfill_parts(days) -> dict:
    """Best ``(unit, value)`` for the period form, from a number of days.

    ``unit`` is ``off``, ``day``, ``week``, ``month``, ``year`` or ``all``.
    """
    try:
        total = int(days or 0)
    except (TypeError, ValueError):
        total = 0
    if total < 0:
        return {"unit": "all", "value": 1}
    if total == 0:
        return {"unit": "off", "value": 1}
    for unit in ("year", "month", "week"):
        factor = BACKFILL_UNIT_DAYS[unit]
        if total % factor == 0:
            return {"unit": unit, "value": total // factor}
    return {"unit": "day", "value": total}


def backfill_label(days) -> str:
    """Human label for the per-feed retroactive period."""
    parts = backfill_parts(days)
    unit, value = parts["unit"], parts["value"]
    if unit == "off":
        return "Desligado"
    if unit == "all":
        return "Todo o acervo do site"
    plural = {"day": "dias", "week": "semanas", "month": "meses", "year": "anos"}[unit]
    if value == 1:
        return {
            "day": "Último 1 dia",
            "week": "Última 1 semana",
            "month": "Último 1 mês",
            "year": "Último 1 ano",
        }[unit]
    return f"Últimos {value} {plural}"


def profile_label(slug: str | None) -> str:
    """Device profile name for a slug (falls back to the raw slug)."""
    from app.devices.registry import all_profiles

    if not slug:
        return "—"
    profile = all_profiles().get(slug)
    return profile.name if profile else slug


def book_reference(book: Book | None) -> str:
    """Short, human reference to a book (used in 'convertido de ...')."""
    if book is None:
        return "arquivo relacionado"
    return book.title
