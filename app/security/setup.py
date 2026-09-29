"""Estado do primeiro acesso (assistente de configuração).

O painel exige que a credencial tenha sido definida **pelo próprio usuário**
(assistente ou tela de Configurações). Enquanto isso não acontece -- instalação
nova, ou instalação antiga com senha vinda do ``.env`` -- todo o painel manda
para ``/setup``. O estado fica em cache porque é consultado a cada requisição.

Como o assistente **cria a credencial**, ele não pode ficar aberto para a
internet: quem chega de fora da rede local precisa informar um token aleatório
que aparece no log do servidor (quem está na mesma máquina/rede não precisa).
"""

from __future__ import annotations

import contextlib
import os
import secrets
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.security import settings_store
from app.security.auth import ADMIN_CONFIRMED_KEY, ADMIN_HASH_KEY

#: Arquivo com o token (0600) para quem não quer/pode caçar no log.
TOKEN_FILENAME = "setup.token"

_configured: bool | None = None
#: Token do primeiro acesso: sorteado a cada boot e mostrado no log.
_token = secrets.token_urlsafe(12)


def token() -> str:
    """Token exigido de quem tenta configurar o servidor de fora da rede local."""
    return _token


def token_required(request) -> bool:
    """True quando o pedido não vem de loopback/rede privada."""
    from app.networking import client_ip, is_local_address

    return not is_local_address(client_ip(request))


def token_ok(request, informado: str | None) -> bool:
    """Valida o token (aceita tudo de dentro da rede local)."""
    if not token_required(request):
        return True
    return secrets.compare_digest(str(informado or ""), _token)


def token_path() -> Path:
    return get_settings().data_dir / TOKEN_FILENAME


def write_token_file() -> Path:
    """Deixa o token em ``DATA_DIR/setup.token`` (0600).

    O log é o caminho principal, mas ele rola e some (e no Docker exige outro
    terminal). Com o arquivo, recuperar o token é um ``cat`` no volume -- o mesmo
    nível de confiança (quem lê o arquivo já tem acesso à máquina).
    """
    path = token_path()
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(_token + "\n")
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return path


def clear_token_file() -> None:
    """Some com o arquivo assim que o primeiro acesso deixa de existir."""
    with contextlib.suppress(OSError):
        token_path().unlink(missing_ok=True)


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
    clear_token_file()


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
