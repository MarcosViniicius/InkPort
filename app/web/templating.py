"""Jinja2 environment for the web panel."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import get_settings
from app.storage.paths import human_size
from app.web.labels import (
    backfill_label,
    backfill_parts,
    content_label,
    converter_label,
    format_label,
    profile_label,
    source_label,
    status_label,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone().strftime("%d/%m/%Y %H:%M")


def _format_relative(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "agora"
    if seconds < 3600:
        return f"{seconds // 60} min atrás"
    if seconds < 86400:
        return f"{seconds // 3600} h atrás"
    return f"{seconds // 86400} d atrás"


def _format_decimal(value, digits: int = 1) -> str:
    """Numbers in the Brazilian style: 12.4 -> "12,4"."""
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:.{digits}f}".replace(".", ",")


def _format_volume(value) -> str:
    """Series volume: 3.0 -> "3" (integers should not show ".0")."""
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else _format_decimal(number)


def static_url(path: str) -> str:
    """``/static/style.css?v=<mtime>`` -- busts the browser cache on every change.

    Without this, a browser can keep an older stylesheet while the HTML is new,
    which makes the whole panel look broken until a hard refresh.
    """
    clean = "/" + path.lstrip("/")
    file_path = STATIC_DIR / path.lstrip("/")
    try:
        version = int(file_path.stat().st_mtime)
    except OSError:
        version = 0
    return f"/static{clean}?v={version}"


def render(request, name: str, context: dict | None = None, *, status_code: int = 200):
    from app.networking import request_base_url

    settings = get_settings()
    base = request_base_url(request, settings)
    base_context = {
        "request": request,
        "app_name": settings.app_name,
        "version": __version__,
        "base_url": base,
        "opds_url": f"{base}/opds",
        "opds2_url": f"{base}/opds/v2",
    }
    base_context.update(context or {})
    return templates.TemplateResponse(request, name, base_context, status_code=status_code)


templates.env.globals.update(
    human_size=human_size,
    format_datetime=_format_datetime,
    format_relative=_format_relative,
    static_url=static_url,
    backfill_label=backfill_label,
    backfill_parts=backfill_parts,
    content_label=content_label,
    converter_label=converter_label,
    format_label=format_label,
    profile_label=profile_label,
    source_label=source_label,
    status_label=status_label,
)
templates.env.filters["datetime"] = _format_datetime
templates.env.filters["relative"] = _format_relative
templates.env.filters["filesize"] = human_size
templates.env.filters["decimal"] = _format_decimal
templates.env.filters["volume"] = _format_volume
