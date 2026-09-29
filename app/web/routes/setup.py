"""Web panel: assistente de primeiro acesso (``/setup``).

Enquanto não existir senha no banco, o painel manda todo mundo para cá. É aqui
que o usuário cria as credenciais e ajusta o essencial -- sem editar ``.env``:
o que ele escolhe vai para o SQLite (cifrado) e passa a valer na hora.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database.base import get_session
from app.security import auth, runtime
from app.security import setup as setup_state
from app.web.templating import render

router = APIRouter(tags=["painel"])

#: A senha mestra do painel pede um mínimo maior que uma senha comum.
MIN_PASSWORD = 8


@router.get("/setup")
def setup_page(request: Request, session: Session = Depends(get_session), token: str = ""):
    if not setup_state.required(session):
        return RedirectResponse("/", status_code=303)
    if not setup_state.token_ok(request, token):
        # De fora da rede local, primeiro o token (que está no log do servidor).
        return render(
            request,
            "setup.html",
            {
                "active": "setup",
                "needs_token": True,
                "token": "",
                "username": auth.admin_username(session),
                "groups": [],
                "fields": [],
                "min_password": MIN_PASSWORD,
            },
            status_code=401,
        )
    return render(
        request,
        "setup.html",
        {
            "active": "setup",
            "needs_token": False,
            "token": token,
            "username": auth.admin_username(session),
            "groups": runtime.groups_for(onboarding=True),
            "fields": runtime.describe(onboarding=True),
            "min_password": MIN_PASSWORD,
        },
    )


@router.post("/setup")
async def setup_submit(request: Request, session: Session = Depends(get_session)):
    if not setup_state.required(session):
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    if not setup_state.token_ok(request, str(form.get("token") or "")):
        return RedirectResponse(
            f"/setup?err={quote('Token de primeiro acesso inválido ou vencido (veja no log do servidor).')}",
            status_code=303,
        )

    username = str(form.get("username") or "").strip() or "admin"
    password = str(form.get("password") or "")
    confirm = str(form.get("confirm_password") or "")

    if len(password) < MIN_PASSWORD:
        return RedirectResponse(
            f"/setup?err={quote(f'A senha precisa de ao menos {MIN_PASSWORD} caracteres.')}",
            status_code=303,
        )
    if password != confirm:
        return RedirectResponse(
            f"/setup?err={quote('As senhas não conferem.')}", status_code=303
        )

    auth.change_password(session, password, username=username)
    setup_state.mark_configured()
    # Só o que veio no formulário vai para o banco. `rendered` diz quais caixas
    # a tela mostrou: ausente ali = desmarcada (False); campo que o assistente
    # nem exibe fica intocado (é o caso de USE_REQUEST_HOST e do login do painel).
    runtime.save(
        session,
        {name: form.get(name) for name in runtime.BY_NAME if name in form},
        rendered=runtime.field_names(onboarding=True),
    )
    auth.login_session(request, username)
    return RedirectResponse(
        f"/?ok={quote('Configuração concluída. Bem-vindo!')}", status_code=303
    )
