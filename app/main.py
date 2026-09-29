"""FastAPI application factory and lifespan wiring."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.api import build_api_router
from app.config import get_settings
from app.database import init_db, session_scope
from app.devices.registry import seed_builtin_profiles
from app.library.repairs import run_repairs
from app.logging_conf import configure_logging
from app.opds import v1_router, v2_router
from app.security import runtime
from app.security import setup as setup_state
from app.security.auth import NotAuthenticated, require_panel
from app.web import STATIC_DIR
from app.web.errors import register_error_handlers
from app.web.routes import build_web_router
from app.workers.manager import WorkerManager

logger = logging.getLogger(__name__)

#: Cabeçalhos de segurança aplicados em toda resposta. A CSP é pragmática:
#: permite o CSS/JS próprios embutidos nos templates, mas bloqueia qualquer
#: origem externa (o projeto não usa CDN) e o enquadramento por terceiros.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), usb=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'"
    ),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    settings.ensure_dirs()

    init_db()
    with session_scope() as session:
        # Configuração salva no painel entra em vigor antes de tudo.
        runtime.load(session)
        configured = setup_state.refresh(session)
        seed_builtin_profiles(session)
        run_repairs(session)

    if not configured:
        logger.warning(
            "primeiro acesso pendente: abra /setup e informe o token %s "
            "(quem acessa da mesma rede/máquina não precisa do token)",
            setup_state.token(),
        )

    _log_access(settings)

    manager = WorkerManager(settings)
    await manager.start()
    app.state.workers = manager
    logger.info("application started", extra={"version": __version__})

    try:
        yield
    finally:
        await manager.stop()
        logger.info("application stopped")


def _secure_cookies(settings) -> bool:
    """Cookies ``Secure`` quando o endereço configurado é HTTPS.

    Sem HTTPS o navegador não enviaria o cookie e o painel não funcionaria, por
    isso a decisão sai do ``BASE_URL``: quem coloca o servidor atrás de um proxy
    com TLS ganha o cookie protegido automaticamente.
    """
    return str(getattr(settings, "base_url", "") or "").strip().lower().startswith("https://")


def _served_over_https(request: Request, settings) -> bool:
    """True quando *esta* requisição chegou por HTTPS (HSTS só faz sentido aí)."""
    from app.networking import request_base_url

    try:
        return request_base_url(request, settings).lower().startswith("https://")
    except Exception:  # noqa: BLE001 - nunca derrubar uma resposta por isso
        return False


def _setup_bypass(path: str) -> bool:
    """Rotas que continuam acessíveis antes da configuração inicial.

    O OPDS fica de fora de propósito: apontar um leitor para o servidor não pode
    esbarrar no assistente (numa instalação nova o acervo ainda está vazio).
    """
    return path.startswith(("/setup", "/static", "/health", "/opds", "/favicon.ico"))


def _log_access(settings) -> None:
    """Print every address where the panel and OPDS can be reached."""
    urls = settings.access_urls
    logger.info(
        "servidor acessível na rede" if settings.on_network else "servidor local",
        extra={"bind": f"{settings.host}:{settings.port}", "urls": urls},
    )
    for url in urls:
        logger.info("  painel: %s   opds: %s/opds", url, url)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Servidor OPDS 1.2/2.0 com painel web e conversão para e-readers.",
        lifespan=lifespan,
        # A documentação da API expõe a superfície inteira: fica atrás do login.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="opds_session",
        max_age=60 * 60 * 24 * 14,
        same_site="lax",
        https_only=_secure_cookies(settings),
    )

    @app.exception_handler(NotAuthenticated)
    async def _redirect_to_login(request: Request, _exc: NotAuthenticated):
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)

    register_error_handlers(app)

    @app.middleware("http")
    async def _bind_request_base_url(request: Request, call_next):
        """Make link building follow the address the client actually used."""
        from app.networking import request_base_url
        from app.request_context import reset_base_url, set_base_url

        token = set_base_url(request_base_url(request, settings))
        try:
            return await call_next(request)
        finally:
            reset_base_url(token)

    @app.middleware("http")
    async def _require_first_setup(request: Request, call_next):
        """Sem senha no banco, o painel inteiro vive no assistente (/setup)."""
        if setup_state.cached() or _setup_bypass(request.url.path):
            return await call_next(request)
        if request.url.path.startswith("/api"):
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Configuração inicial pendente: abra o painel em /setup."
                },
            )
        return RedirectResponse("/setup", status_code=303)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        """Cabeçalhos de segurança em toda resposta (e HSTS quando há HTTPS)."""
        response = await call_next(request)
        for nome, valor in SECURITY_HEADERS.items():
            response.headers.setdefault(nome, valor)
        if _served_over_https(request, settings):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=15552000; includeSubDomains"
            )
        return response

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(v1_router)
    app.include_router(v2_router)
    app.include_router(build_api_router())
    app.include_router(build_web_router())

    @app.get("/api/openapi.json", include_in_schema=False)
    def api_schema(request: Request):
        """Especificação da API, atrás do login.

        A interface do Swagger foi deixada de fora de propósito: ela carrega
        JavaScript de CDN, e o projeto não depende de internet. Use a
        especificação em qualquer cliente local (Insomnia, Postman, Postman...).
        """
        require_panel(request)
        return JSONResponse(app.openapi())

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
