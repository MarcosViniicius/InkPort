"""Freio de força bruta no login (em memória, sem dependência externa).

Cinco falhas seguidas para o mesmo ``usuário@ip`` e o login passa a esperar:
as tentativas seguintes ficam bloqueadas por alguns minutos. O contador zera no
primeiro acerto.

É um freio **local**, suficiente para um servidor pessoal: não substitui HTTPS
(nem senha forte) e reinicia quando o processo reinicia. Onde houver vários
workers ou instâncias, use um proxy com rate limit na frente.
"""

from __future__ import annotations

import time

#: Falhas toleradas antes de bloquear.
MAX_FAILURES = 5
#: Janela em que as falhas contam umas com as outras.
WINDOW_SECONDS = 900
#: Tempo de bloqueio depois de estourar o limite.
LOCK_SECONDS = 300

_failures: dict[str, list[float]] = {}


def key(username: str, address: str) -> str:
    """Chave do freio: combina usuário e origem (não adianta só trocar de IP)."""
    return f"{(username or '?').strip().lower()}@{(address or '?').strip()}"


def blocked_for(chave: str, *, now: float | None = None) -> int:
    """Segundos restantes de bloqueio (0 = pode tentar)."""
    momento = time.monotonic() if now is None else now
    marcas = _prune(chave, momento)
    if len(marcas) < MAX_FAILURES:
        return 0
    restante = LOCK_SECONDS - (momento - marcas[-1])
    return max(0, int(restante))


def register_failure(chave: str, *, now: float | None = None) -> int:
    """Anota uma falha e devolve os segundos de bloqueio (0 = ainda livre)."""
    momento = time.monotonic() if now is None else now
    marcas = _prune(chave, momento)
    marcas.append(momento)
    _failures[chave] = marcas
    return blocked_for(chave, now=momento)


def reset(chave: str) -> None:
    """Limpa o histórico (chamado quando o login dá certo)."""
    _failures.pop(chave, None)


def clear() -> None:
    """Zera tudo (usado pelos testes)."""
    _failures.clear()


def _prune(chave: str, now: float) -> list[float]:
    marcas = [m for m in _failures.get(chave, []) if now - m < WINDOW_SECONDS]
    if marcas:
        _failures[chave] = marcas
    else:
        _failures.pop(chave, None)
    return marcas
