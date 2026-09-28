"""Friendly errors for the web panel.

The API and OPDS keep answering JSON/Atom; anything a person clicks must come
back as a readable page (or a flash on the form they came from) that says what
happened and what to do next.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from urllib.parse import quote, urlparse

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.web.templating import render

logger = logging.getLogger(__name__)

#: Paths that speak JSON / Atom instead of HTML.
MACHINE_PREFIXES = ("/api", "/opds", "/static", "/health")

#: status -> (title, explanation)
PAGES: dict[int, tuple[str, str]] = {
    400: ("Não foi possível usar esses dados", "Confira o que foi enviado e tente de novo."),
    401: ("Você precisa entrar", "Faça login para continuar."),
    403: ("Você não tem permissão para isso", "Sua sessão pode ter expirado — entre novamente."),
    404: ("Não encontramos esta página", "O endereço pode ter mudado, ou o item foi removido."),
    405: ("Este endereço não aceita essa ação", "Use os botões do painel para fazê-la."),
    409: ("Isso não pode ser feito agora", "O estado mudou; recarregue a página e tente de novo."),
    413: ("O arquivo é grande demais", "Ajuste o limite de upload em Configurações se precisar."),
    422: ("Confira os campos do formulário", "Algum campo está vazio ou com formato inválido."),
    500: (
        "Algo deu errado no servidor",
        "A ação não foi concluída. Tente de novo e, se persistir, veja os logs.",
    ),
    503: ("O servidor está ocupado", "Tente de novo em alguns instantes."),
}


def is_machine_request(request: Request) -> bool:
    path = request.url.path
    if path.startswith(MACHINE_PREFIXES):
        return True
    accept = request.headers.get("accept", "")
    return "text/html" not in accept and "application/json" in accept


def page_for(status: int, detail: str | None = None) -> tuple[str, str]:
    """Title and message for a status code, preferring a custom detail."""
    title, message = PAGES.get(status, ("Não foi possível concluir a ação", "Tente de novo."))
    if detail and detail.strip() and detail != _reason(status):
        message = detail.strip()
    return title, message


def _reason(status: int) -> str:
    """Starlette's default detail is the HTTP phrase ("Not Found")."""
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


def panel_error_response(request: Request, status: int, detail: str | None = None) -> Response:
    """Redirect form posts back with a flash; render a page for the rest."""
    title, message = page_for(status, detail)

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        back = _safe_referer(request)
        if back:
            joiner = "&" if "?" in back else "?"
            return RedirectResponse(f"{back}{joiner}err={quote(message)}", status_code=303)

    return render(
        request,
        "error.html",
        {"status": status, "title": title, "message": message},
        status_code=status,
    )


def register_error_handlers(app) -> None:
    """Attach the panel-friendly handlers to the app."""

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> Response:
        """A malformed filter must not answer the panel with raw JSON.

        FastAPI's default is a 422 JSON body, which a person on the panel cannot
        use (that is how an empty ``category_id`` used to break the library
        filter).
        """
        if is_machine_request(request):
            return JSONResponse({"detail": exc.errors()}, status_code=422)
        fields = [
            ".".join(str(part) for part in error.get("loc", ()) if part != "query")
            for error in exc.errors()[:4]
        ]
        logger.info(
            "panel request had invalid input",
            extra={"path": request.url.path, "fields": fields},
        )
        message = "Confira os campos da busca"
        if fields:
            message += ": " + ", ".join(fields)
        return panel_error_response(request, 422, message + ".")

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> Response:
        if is_machine_request(request):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        if exc.status_code == 401:
            # Not logged in: send to the login page instead of an error.
            return RedirectResponse(
                f"/login?next={quote(request.url.path)}", status_code=303
            )
        logger.info(
            "panel request failed",
            extra={
                "path": request.url.path,
                "status": exc.status_code,
                "detail": exc.detail,
            },
        )
        return panel_error_response(
            request, exc.status_code, str(exc.detail) if exc.detail else None
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> Response:
        logger.exception("unhandled error", extra={"path": request.url.path})
        if is_machine_request(request):
            return JSONResponse({"detail": "Erro interno do servidor."}, status_code=500)
        return panel_error_response(request, 500)


def _safe_referer(request: Request) -> str | None:
    """Same-origin referer path, if any (used to return to the form)."""
    referer = request.headers.get("referer")
    if not referer:
        return None
    parsed = urlparse(referer)
    if parsed.netloc and parsed.netloc != request.headers.get("host"):
        return None
    path = parsed.path or ""
    if not path or path.startswith(MACHINE_PREFIXES):
        return None
    return path
