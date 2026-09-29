"""Authentication: panel session login and optional OPDS Basic auth."""

from __future__ import annotations

import logging
import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session

from app.config import get_base_settings, get_settings
from app.database.base import get_session
from app.security import passwords, runtime, settings_store

logger = logging.getLogger(__name__)

ADMIN_USER_KEY = "admin_username"
ADMIN_HASH_KEY = "admin_password_hash"
SESSION_USER_KEY = "user"

_basic = HTTPBasic(auto_error=False)


class NotAuthenticated(Exception):
    """Raised for HTML routes; the app redirects to the login page."""


# --- credential storage ---------------------------------------------------
def ensure_admin(session: Session) -> bool:
    """Create the admin from the configuration only when a password was set.

    A password is "set" when it comes from the environment or ``.env`` (even the
    old default ``admin``): that is a deliberate choice and is respected. With
    nothing configured, the panel stays in wizard mode and the user creates the
    credentials in ``/setup`` -- so the ``.env`` is optional.
    Returns True when an admin exists afterwards.
    """
    if settings_store.get(session, ADMIN_HASH_KEY) is not None:
        return True
    settings = get_base_settings()
    explicit = "admin_password" in settings.model_fields_set
    password = (settings.admin_password or "").strip()
    if not password or not explicit:
        logger.info("sem senha configurada: o assistente de primeiro acesso vai rodar")
        return False
    settings_store.set_value(session, ADMIN_USER_KEY, settings.admin_username or "admin")
    settings_store.set_value(session, ADMIN_HASH_KEY, passwords.hash_password(password))
    session.commit()
    logger.info("admin criado a partir da configuração (senha definida por você)")
    return True


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
    stored = settings_store.get(session, ADMIN_USER_KEY)
    if stored:
        return stored
    return runtime.get("admin_username") or get_settings().admin_username or "admin"


# --- panel (cookie session) ----------------------------------------------
def login_session(request: Request, username: str) -> None:
    request.session[SESSION_USER_KEY] = username


def logout_session(request: Request) -> None:
    request.session.clear()


def current_user(request: Request) -> str | None:
    return request.session.get(SESSION_USER_KEY)


def require_panel(request: Request) -> str:
    if not runtime.get("require_auth_panel"):
        return "guest"
    user = current_user(request)
    if not user:
        raise NotAuthenticated()
    return user


PanelUser = Depends(require_panel)


# --- OPDS (HTTP Basic) ----------------------------------------------------
def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais OPDS inválidas",
        headers={"WWW-Authenticate": 'Basic realm="OPDS"'},
    )


def require_opds_auth(
    credentials: HTTPBasicCredentials | None = Depends(_basic),
    session: Session = Depends(get_session),
) -> None:
    username = runtime.get("opds_username") or ""
    if not username:
        return  # OPDS is open

    if credentials is None or not secrets.compare_digest(credentials.username, username):
        raise _unauthorized()

    # No banco a senha é guardada como hash; no .env antigo, em texto puro.
    stored_hash = runtime.get("opds_password")
    if stored_hash:
        allowed = passwords.verify_password(credentials.password, stored_hash)
    else:
        allowed = secrets.compare_digest(credentials.password, get_settings().opds_password)
    if not allowed:
        raise _unauthorized()
