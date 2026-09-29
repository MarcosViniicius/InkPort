"""Web panel: login and logout.

O login é o único ponto de entrada do painel: aqui ele tem freio de força bruta
(``security/throttle``), registro das falhas e destino sempre interno
(``auth.safe_next`` protege contra redirecionamento para outro site).
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database.base import get_session
from app.networking import client_ip
from app.security import auth, throttle
from app.web.templating import render

logger = logging.getLogger(__name__)
router = APIRouter(tags=["painel"])

#: Pausa fixa por falha: encarece automação sem irritar quem só errou a senha.
FAILURE_DELAY_SECONDS = 0.4


@router.get("/login")
def login_form(request: Request, next: str = "/"):
    if auth.current_user(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", {"next": auth.safe_next(next), "error": None})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    session: Session = Depends(get_session),
):
    destino = auth.safe_next(next)
    origem = client_ip(request)
    chave = throttle.key(username, origem)

    espera = throttle.blocked_for(chave)
    if espera:
        logger.warning("login bloqueado", extra={"user": username, "ip": origem, "espera": espera})
        return render(
            request,
            "login.html",
            {
                "next": destino,
                "username": username,
                "error": f"Muitas tentativas. Aguarde {espera} s e tente de novo.",
            },
            status_code=429,
        )

    if not auth.verify_credentials(session, username, password):
        bloqueio = throttle.register_failure(chave)
        time.sleep(FAILURE_DELAY_SECONDS)
        logger.warning(
            "login falhou", extra={"user": username, "ip": origem, "bloqueio": bloqueio}
        )
        aviso = "Usuário ou senha inválidos."
        if bloqueio:
            aviso += f" Novas tentativas bloqueadas por {bloqueio} s."
        return render(
            request,
            "login.html",
            {"next": destino, "error": aviso, "username": username},
        )

    throttle.reset(chave)
    auth.login_session(request, username)
    logger.info("login ok", extra={"user": username, "ip": origem})
    return RedirectResponse(destino, status_code=303)


@router.get("/logout")
def logout(request: Request):
    auth.logout_session(request)
    return RedirectResponse("/login", status_code=303)
