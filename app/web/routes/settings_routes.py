"""Web panel: settings, security and maintenance."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.converters.tools import detect_toolchain
from app.database.base import get_session
from app.library.repairs import run_repairs
from app.library.service import mark_missing
from app.security import auth, runtime
from app.security.auth import require_panel
from app.storage.temp import clean_temp_dir
from app.storage.usage import library_usage
from app.updates import service as update_service
from app.web.templating import render
from app.workers import queue

router = APIRouter(prefix="/settings", dependencies=[Depends(require_panel)], tags=["painel"])


@router.get("")
def settings_page(request: Request, session: Session = Depends(get_session)):
    settings = get_settings()
    from app.downloads.cleanup import CleanupRule
    from app.downloads.cleanup import plan as plan_cleanup

    cleanup_rule = CleanupRule.from_settings(settings)
    cleanup = plan_cleanup(session, cleanup_rule)
    return render(
        request,
        "settings.html",
        {
            "active": "settings",
            "settings": settings,
            "usage": library_usage(),
            "tools": detect_toolchain().as_dict(),
            "admin_username": auth.admin_username(session),
            "queue": queue.counts_by_status(session),
            "network_urls": settings.access_urls,
            "on_network": settings.on_network,
            "groups": runtime.groups_for(onboarding=False),
            "fields": runtime.describe(),
            "opds_problem": auth.opds_protection_problem(session),
            "cleanup_plan": cleanup,
            "cleanup_bytes": sum(row["size_bytes"] for row in cleanup),
            "cleanup_enabled": bool(getattr(settings, "download_cleanup_enabled", False)),
            "cleanup_days": cleanup_rule.days,
            "cleanup_include_originals": cleanup_rule.include_originals,
            "update_status": update_service.details(session),
        },
    )


@router.post("/app")
async def save_app_settings(request: Request, session: Session = Depends(get_session)):
    """Grava a configuração da aplicação (a mesma do assistente de primeiro acesso)."""
    from urllib.parse import quote

    form = await request.form()
    warnings = runtime.save(
        session,
        {name: form.get(name) for name in runtime.BY_NAME if name in form},
        rendered=runtime.field_names(),
    )
    if warnings:
        return RedirectResponse(
            f"/settings?err={quote(' '.join(warnings))}", status_code=303
        )
    return RedirectResponse(
        f"/settings?ok={quote('Configuração salva.')}", status_code=303
    )


@router.post("/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    username: str = Form(""),
    session: Session = Depends(get_session),
):
    from urllib.parse import quote

    if new_password != confirm_password:
        return RedirectResponse(
            f"/settings?err={quote('As senhas não conferem.')}", status_code=303
        )
    if len(new_password) < 6:
        return RedirectResponse(
            f"/settings?err={quote('A senha deve ter ao menos 6 caracteres.')}",
            status_code=303,
        )
    if not auth.verify_credentials(session, auth.admin_username(session), current_password):
        return RedirectResponse(
            f"/settings?err={quote('A senha atual está incorreta.')}", status_code=303
        )
    auth.change_password(session, new_password, username=username.strip() or None)
    return RedirectResponse(
        f"/settings?ok={quote('Credenciais atualizadas.')}", status_code=303
    )


@router.post("/updates/check")
async def updates_check_now():
    """Start an update check in the background and return immediately.

    The check does network I/O (git ls-remote/fetch), so it must not block the
    request — same pattern as the feed refresh. The result appears in the
    settings section when it finishes.
    """
    ok, reason = update_service.preflight()
    if not ok:
        return RedirectResponse(
            f"/settings?err={quote(reason)}#atualizacoes", status_code=303
        )
    asyncio.create_task(
        asyncio.to_thread(update_service.check_updates_in_background)
    )
    return RedirectResponse(
        f"/settings?ok={quote('Verificação iniciada — o resultado aparece aqui em instantes.')}"
        "#atualizacoes",
        status_code=303,
    )


@router.post("/updates/apply")
async def updates_apply_now():
    """Pull the update and, on Docker, rebuild the stack — in the background.

    A pull plus a compose rebuild takes minutes; blocking the request would
    make the panel look frozen. The outcome is persisted and shown in the
    settings section.
    """
    ok, reason = update_service.preflight()
    if not ok:
        return RedirectResponse(
            f"/settings?err={quote(reason)}#atualizacoes", status_code=303
        )
    asyncio.create_task(
        asyncio.to_thread(update_service.apply_update_in_background)
    )
    return RedirectResponse(
        f"/settings?ok={quote('Atualização iniciada em segundo plano — acompanhe o resultado aqui.')}"
        "#atualizacoes",
        status_code=303,
    )


@router.post("/maintenance")
def maintenance(session: Session = Depends(get_session)):
    from urllib.parse import quote

    clean_temp_dir(max_age_seconds=0)
    queue.requeue_stale(session, older_than_seconds=1800)
    mark_missing(session)
    run_repairs(session)
    return RedirectResponse(
        f"/settings?ok={quote('Manutenção concluída.')}", status_code=303
    )


@router.post("/cleanup")
def cleanup_now(session: Session = Depends(get_session)):
    """Run the download cleanup now with the current rules."""
    from urllib.parse import quote

    from app.downloads.cleanup import CleanupRule
    from app.downloads.cleanup import run as run_cleanup
    from app.storage.paths import human_size

    result = run_cleanup(session, CleanupRule.from_settings(get_settings()))
    if result["removed"]:
        message = (
            f"Limpeza: {result['removed']} arquivo(s) removido(s) "
            f"({human_size(result['freed_bytes'])})."
        )
    else:
        message = "Nada para limpar agora."
    return RedirectResponse(f"/settings?ok={quote(message)}", status_code=303)
