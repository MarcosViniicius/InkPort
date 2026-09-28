"""Shared dependencies for the internal REST API."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Query, Request, status

from app.config import get_settings
from app.security.auth import current_user


@dataclass(slots=True)
class Pagination:
    page: int
    per_page: int


def pagination(
    page: int = Query(1, ge=1),
    per_page: int = Query(24, ge=1, le=200),
) -> Pagination:
    return Pagination(page=page, per_page=per_page)


def require_api(request: Request) -> str:
    """JSON-friendly auth guard for the internal API."""
    settings = get_settings()
    if not settings.require_auth_panel:
        return "guest"
    user = current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Não autenticado"
        )
    return user
