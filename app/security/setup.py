"""Estado do primeiro acesso (assistente de configuração).

Enquanto não existir senha no banco, o painel inteiro manda o usuário para
``/setup``. O estado fica em cache porque isso é consultado a cada requisição;
qualquer gravação de credencial atualiza o cache.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.security import settings_store
from app.security.auth import ADMIN_HASH_KEY

_configured: bool | None = None


def required(session: Session) -> bool:
    """True while the panel has no password yet (the wizard must run)."""
    return not configured(session)


def configured(session: Session) -> bool:
    global _configured
    if _configured is None:
        _configured = settings_store.get(session, ADMIN_HASH_KEY) is not None
    return _configured


def refresh(session: Session) -> bool:
    """Re-read the state from the database (boot, after saving credentials)."""
    global _configured
    _configured = settings_store.get(session, ADMIN_HASH_KEY) is not None
    return _configured


def mark_configured() -> None:
    """Called right after the wizard (or a password change) creates the admin."""
    global _configured
    _configured = True


def cached() -> bool:
    """Cached state, for the middleware (boot fills it).

    Without a cached answer we do **not** block the panel: melhor deixar passar
    do que trancar o usuário fora do próprio servidor.
    """
    return True if _configured is None else _configured


def reset() -> None:
    """Forget the cached state (tests)."""
    global _configured
    _configured = None
