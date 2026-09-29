"""Estado do primeiro acesso (assistente de configuração).

O painel exige que a credencial tenha sido definida **pelo próprio usuário**
(assistente ou tela de Configurações). Enquanto isso não acontece -- instalação
nova, ou instalação antiga com senha vinda do ``.env`` -- todo o painel manda
para ``/setup``. O estado fica em cache porque é consultado a cada requisição.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.security import settings_store
from app.security.auth import ADMIN_CONFIRMED_KEY, ADMIN_HASH_KEY

_configured: bool | None = None


def required(session: Session) -> bool:
    """True while the panel credentials were not created in the panel itself."""
    return not configured(session)


def configured(session: Session) -> bool:
    global _configured
    if _configured is None:
        _configured = _read(session)
    return _configured


def refresh(session: Session) -> bool:
    """Re-read the state from the database (boot, after saving credentials)."""
    global _configured
    _configured = _read(session)
    return _configured


def _read(session: Session) -> bool:
    """A password exists *and* was confirmed by the user in the panel."""
    has_password = settings_store.get(session, ADMIN_HASH_KEY) is not None
    confirmed = settings_store.get(session, ADMIN_CONFIRMED_KEY) is not None
    return bool(has_password and confirmed)


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
