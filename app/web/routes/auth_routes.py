"""Web panel: login and logout."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database.base import get_session
from app.security import auth
from app.web.templating import render

router = APIRouter(tags=["painel"])


@router.get("/login")
def login_form(request: Request, next: str = "/"):
    if auth.current_user(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", {"next": next, "error": None})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    session: Session = Depends(get_session),
):
    if not auth.verify_credentials(session, username, password):
        return render(
            request,
            "login.html",
            {"next": next, "error": "Usuário ou senha inválidos.", "username": username},
        )
    auth.login_session(request, username)
    destination = next if next.startswith("/") else "/"
    return RedirectResponse(destination, status_code=303)


@router.get("/logout")
def logout(request: Request):
    auth.logout_session(request)
    return RedirectResponse("/login", status_code=303)
