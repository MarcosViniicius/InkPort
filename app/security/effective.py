"""Settings efetivas: ``.env`` como padrão, banco como verdade.

O ``.env`` é o bootstrap (caminhos, host, porta, log, cifragem) e o valor de
fallback; o que o usuário configura no painel fica no SQLite. Esta classe expõe
a **mesma interface** de :class:`app.config.Settings` -- quem já chamava
``get_settings().alguma_coisa`` passa a receber o valor do banco sem mudar nada.

Só os nomes descritos em ``security/runtime.FIELDS`` são sobrepostos; o resto
(caminhos, host, porta, log) é delegado ao objeto do ``.env``.
"""

from __future__ import annotations

from app.config import Settings
from app.security import runtime


class EffectiveSettings:
    """Read-only overlay of :class:`Settings` with the database values."""

    __slots__ = ("_base",)

    def __init__(self, base: Settings) -> None:
        object.__setattr__(self, "_base", base)

    # -- valores sobreponíveis --------------------------------------------
    def __getattr__(self, name: str):
        if name in runtime.BY_NAME:
            return runtime.get(name)
        return getattr(self._base, name)

    def __setattr__(self, name: str, value) -> None:  # pragma: no cover - guard
        raise AttributeError("EffectiveSettings é somente leitura; use runtime.save()")

    # -- derivados que dependem de valores configuráveis --------------------
    @property
    def base_url_clean(self) -> str:
        from app.networking import resolve_base_url

        return resolve_base_url(runtime.get("base_url") or "", self._base.host, self._base.port)

    def as_dict(self) -> dict:
        """Todos os valores efetivos (para /api/system/settings e testes)."""
        effective = self._base.model_dump()
        effective.update(runtime.values())
        return effective

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"EffectiveSettings(base={self._base.app_name!r})"
