"""Authentication: panel session login and optional OPDS Basic auth."""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.base import get_session
from app.security import passwords, settings_store

ADMIN_USER_KEY = "admin_username"
ADMIN_HASH_KEY = "admin_password_hash"
SESSION_USER_KEY = "user"

_basic = HTTPBasic(auto_error=False)


class NotAuthenticated(Exception):
    """Raised for HTML routes; the app redirects to the login page."""


# --- credential storage ---------------------------------------------------
def ensure_admin(session: Session) -> None:
    """Create the initial admin account from ``.env`` on first start."""
    settings = get_settings()
    if settings_store.get(session, ADMIN_HASH_KEY) is None:
        settings_store.set_value(session, ADMIN_USER_KEY, settings.admin_username)
        settings_store.set_value(
            session, ADMIN_HASH_KEY, passwords.hash_password(settings.admin_password)
        )
        session.commit()


def verify_credentials(session: Session, username: str, password: str) -> bool:
    stored_user = settings_store.get(session, ADMIN_USER_KEY, "")
    stored_hash = settings_store.get(session, ADMIN_HASH_KEY)
    if not username or not stored_user:
        return False
    if not secrets.compare_digest(username, stored_user):
        return False
    return passwords.verify_password(password, stored_hash)


def change_password(session: Session, password: str, *, username: str | None = None) -> None:
    if username:
        settings_store.set_value(session, ADMIN_USER_KEY, username)
    settings_store.set_value(session, ADMIN_HASH_KEY, passwords.hash_password(password))
    session.commit()


def admin_username(session: Session) -> str:
    return settings_store.get(session, ADMIN_USER_KEY, get_settings().admin_username) or "admin"


# --- panel (cookie session) ----------------------------------------------
def login_session(request: Request, username: str) -> None:
    request.session[SESSION_USER_KEY] = username


def logout_session(request: Request) -> None:
    request.session.clear()


def current_user(request: Request) -> str | None:
    return request.session.get(SESSION_USER_KEY)


def require_panel(request: Request) -> str:
    settings = get_settings()
    if not settings.require_auth_panel:
        return "guest"
    user = current_user(request)
    if not user:
        raise NotAuthenticated()
    return user


PanelUser = Depends(require_panel)


# --- OPDS (HTTP Basic) ----------------------------------------------------
def require_opds_auth(
    credentials: HTTPBasicCredentials | None = Depends(_basic),
    session: Session = Depends(get_session),
) -> None:
    settings = get_settings()
    if not settings.opds_username:
        return  # OPDS is open

    if credentials is None or not (
        secrets.compare_digest(credentials.username, settings.opds_username)
        and secrets.compare_digest(credentials.password, settings.opds_password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais OPDS inválidas",
            headers={"WWW-Authenticate": 'Basic realm="OPDS"'},
        )
