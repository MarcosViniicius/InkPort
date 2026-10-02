"""Configuração viva no banco (o que antes só existia no ``.env``).

O ``.env`` passa a ser apenas *bootstrap* (``DATA_DIR``, ``HOST``, ``PORT``,
``LOG_*``) e valor de **fallback**: o que é configurável fica no SQLite, lido
de um cache em memória para o resto do app consultar sem abrir sessão.

O registro :data:`FIELDS` descreve cada ajuste (rótulo, ajuda, tipo, limites).
O assistente de primeiro acesso e a tela de Configurações são gerados a partir
dele -- uma descrição só, sem duplicar rótulo nem validação.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_base_settings
from app.security import passwords, settings_store

logger = logging.getLogger(__name__)

#: Rótulo dos grupos usados pela interface. A tela de Configurações desenha uma
#: seção expansível por grupo, então o rótulo é o título que o usuário vê.
GROUPS: dict[str, str] = {
    "opds": "Aplicação e catálogo",
    "access": "Acesso ao painel",
    "app": "Aplicação",
    "network": "Rede e endereços",
    "conversion": "Conversão e armazenamento",
    "automation": "Automação",
    "cleanup": "Limpeza automática",
}


@dataclass(frozen=True, slots=True)
class Field:
    """One configurable setting, described once for every screen."""

    name: str
    label: str
    group: str = "app"
    kind: str = "text"  # text | int | float | bool | choice | password
    help: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    #: Valor efetivo quando não há campo equivalente em ``Settings`` (o ``.env``).
    default: Any = None
    secret: bool = False
    #: Só aparece no assistente de primeiro acesso (configurações essenciais).
    onboarding: bool = True
    #: Vai para o bloco «Avançado» da seção na tela de Configurações.
    advanced: bool = False

    @property
    def store_key(self) -> str:
        """Chave no banco: segredos são guardados como hash, nunca em claro."""
        return f"{self.name}_hash" if self.secret else self.name


#: Tudo que é configurável pelo usuário. ``name`` coincide com o campo de
#: ``Settings`` quando existe, para o ``.env`` continuar servindo de padrão.
#:
#: A ordem aqui é a ordem das telas. O assistente mostra só os campos
#: ``onboarding`` -- por isso as credenciais do OPDS vêm logo no início: é o que
#: o usuário precisa para configurar o leitor.
#:
#: ``advanced`` tira o campo do caminho principal: na tela de Configurações ele
#: vai para um bloco «Avançado» dentro da própria seção (não some, só espera).
FIELDS: tuple[Field, ...] = (
    Field(
        "app_name",
        "Nome da aplicação",
        group="opds",
        help="Aparece no painel, no título das páginas e no feed OPDS.",
    ),
    Field(
        "opds_username",
        "Usuário do OPDS (para o leitor)",
        group="opds",
        help=(
            "O que você vai digitar no aplicativo/leitor para baixar os livros. "
            "Escolha um nome curto (ex.: leitor)."
        ),
    ),
    Field(
        "opds_password",
        "Senha do OPDS (para o leitor)",
        group="opds",
        kind="password",
        secret=True,
        minimum=6,
        help=(
            "A mesma senha que você digita no leitor. Fica guardada como hash: "
            "nem esta aplicação consegue lê-la de volta. Deixe em branco para "
            "manter a atual."
        ),
    ),
    Field(
        "opds_require_auth",
        "Exigir usuário e senha no catálogo",
        group="opds",
        kind="bool",
        default=True,
        help=(
            "Ligado (recomendado): o catálogo só responde com as credenciais "
            "acima — ninguém lê sua biblioteca sem elas. Desligue apenas em rede "
            "local confiável."
        ),
    ),
    Field(
        "opds_root_mode",
        "Raiz do OPDS",
        group="opds",
        kind="choice",
        help=(
            "O que a página inicial do catálogo entrega. «Livros e menus» "
            "funciona em qualquer leitor; se o seu só lista o que tem link de "
            "download, use «Só livros» ou aponte para o catálogo do aparelho "
            "em Dispositivos."
        ),
        choices=(
            ("mixed", "Livros e menus"),
            ("navigation", "Só menus"),
            ("books", "Só livros"),
        ),
    ),
    Field(
        "require_auth_panel",
        "Exigir login no painel",
        group="access",
        kind="bool",
        help=(
            "Desligue apenas em rede totalmente confiável: sem login, qualquer "
            "pessoa que alcançar este endereço abre a biblioteca e as "
            "configurações."
        ),
        # Fora do assistente: ele não pode desligar o login sem o usuário pedir.
        onboarding=False,
    ),
    Field(
        "base_url",
        "Endereço base",
        group="network",
        help=(
            "Vazio = detecta sozinho. Preencha com https:// quando houver proxy "
            "reverso ou domínio (isso também protege o cookie de sessão)."
        ),
        advanced=True,
    ),
    Field(
        "use_request_host",
        "Seguir o endereço do cliente",
        group="network",
        kind="bool",
        help="Monta os links com o host que o cliente usou (funciona por Tailscale/VPN).",
        onboarding=False,
        advanced=True,
    ),
    Field(
        "conversion_concurrency",
        "Conversões simultâneas",
        group="conversion",
        kind="int",
        minimum=1,
        maximum=8,
        help="Mais paralelismo = mais RAM. Vale depois de reiniciar o servidor.",
    ),
    Field(
        "max_upload_mb",
        "Limite de upload (MB)",
        group="conversion",
        kind="int",
        minimum=1,
        maximum=10240,
    ),
    Field(
        "storage_limit_gb",
        "Teto de armazenamento (GB)",
        group="conversion",
        kind="float",
        minimum=0,
        maximum=102400,
        help="0 = sem teto.",
    ),
    Field(
        "conversion_timeout",
        "Tempo máximo por conversão (s)",
        group="conversion",
        kind="int",
        minimum=60,
        maximum=7200,
        help="Depois disso o job é interrompido.",
        onboarding=False,
        advanced=True,
    ),
    Field(
        "manga_max_scale",
        "Ampliação máxima de texto (mangá)",
        group="conversion",
        kind="float",
        minimum=1.0,
        maximum=4.0,
        default=2.0,
        help=(
            "Quanto o texto de quadrinhos pode crescer no máximo (2 = o dobro). "
            "Vale para conversões com «Ampliar textos» ligada."
        ),
        onboarding=False,
        advanced=True,
    ),
    Field(
        "manga_overflow",
        "Ampliação além do balão (mangá)",
        group="conversion",
        kind="float",
        minimum=0.0,
        maximum=1.0,
        default=0.15,
        help=(
            "Quanto o texto pode passar do balão quando quase não há margem "
            "(0 = não passa). Só avança sobre espaço livre; a arte é preservada."
        ),
        onboarding=False,
        advanced=True,
    ),
    Field(
        "manga_max_overflow_px",
        "Limite da ampliação além do balão (px)",
        group="conversion",
        kind="int",
        minimum=0,
        maximum=64,
        default=10,
        help="Teto em pixels de quanto o texto pode sair do balão.",
        onboarding=False,
        advanced=True,
    ),
    Field(
        "manga_ocr_lang",
        "Idioma do OCR de mangá",
        group="conversion",
        kind="choice",
        choices=(
            ("pt", "Português"),
            ("en", "Inglês"),
            ("es", "Espanhol"),
            ("fr", "Francês"),
            ("de", "Alemão"),
            ("it", "Italiano"),
        ),
        default="pt",
        help="Idioma dos textos reconhecidos no modo preciso.",
        onboarding=False,
        advanced=True,
    ),
    Field(
        "manga_ocr_model",
        "Modelo do OCR de mangá",
        group="conversion",
        kind="choice",
        choices=(
            ("tiny", "Rápido (menos preciso)"),
            ("small", "Equilibrado"),
            ("medium", "Preciso (mais lento)"),
        ),
        default="small",
        help="Modelos ONNX baixados uma única vez (~5 a 35 MB).",
        onboarding=False,
        advanced=True,
    ),
    Field(
        "manga_ocr_min_score",
        "Confiança mínima do OCR",
        group="conversion",
        kind="float",
        minimum=0.1,
        maximum=0.95,
        default=0.5,
        help="Abaixo disso o texto é ignorado e a região fica intacta.",
        onboarding=False,
        advanced=True,
    ),
    Field(
        "manga_font_min_score",
        "Confiança mínima para reescrever com fonte",
        group="conversion",
        kind="float",
        minimum=0.1,
        maximum=0.99,
        default=0.75,
        help=(
            "No modo que reescreve o texto, abaixo disso a região usa os "
            "glifos originais em vez da fonte."
        ),
        onboarding=False,
        advanced=True,
    ),
    Field(
        "manga_font",
        "Fonte do texto reescrito",
        group="conversion",
        help=(
            "Vazio usa a Comic Neue embutida. Informe o caminho de um arquivo "
            ".ttf/.otf para trocar."
        ),
        onboarding=False,
        advanced=True,
    ),
    Field(
        "rss_worker_enabled",
        "Buscar feeds automaticamente",
        group="automation",
        kind="bool",
        help="Desligado, os feeds só são atualizados quando você pedir no painel.",
    ),
    Field(
        "download_cleanup_enabled",
        "Limpeza automática de arquivos",
        group="cleanup",
        kind="bool",
        help=(
            "Remove do disco os arquivos marcados como baixados depois do prazo "
            "abaixo. O histórico de downloads é mantido."
        ),
        onboarding=False,
    ),
    Field(
        "download_cleanup_days",
        "Remover depois de (dias)",
        group="cleanup",
        kind="int",
        minimum=1,
        maximum=3650,
        default=30,
        help="Contado desde o último download (ou desde o bloqueio).",
        onboarding=False,
    ),
    Field(
        "download_cleanup_include_originals",
        "Incluir arquivos originais",
        group="cleanup",
        kind="bool",
        default=False,
        help=(
            "Desligado, a limpeza remove apenas arquivos gerados por conversão — "
            "os originais ficam. Ligue para incluir tudo."
        ),
        onboarding=False,
        advanced=True,
    ),
)

_BY_NAME = {field.name: field for field in FIELDS}
#: Público para o overlay de settings (``security/effective.py``) e para testes.
BY_NAME = _BY_NAME
_cache: dict[str, Any] = {}
_loaded = False


# --- leitura --------------------------------------------------------------
def reset() -> None:
    """Forget everything (boot and tests)."""
    global _loaded
    _cache.clear()
    _loaded = False


def load(session: Session) -> None:
    """Fill the cache from the database; missing keys fall back to the env."""
    global _loaded
    stored = settings_store.all_values(session)
    _cache.clear()
    for field in FIELDS:
        raw = stored.get(field.store_key)
        if raw is None:
            continue
        value = _from_storage(field, raw)
        if value is not None:
            _cache[field.name] = value
    _loaded = True


def get(name: str, default: Any = None) -> Any:
    """Current value: database first, then the ``.env`` default."""
    if name in _cache:
        return _cache[name]
    if default is not None:
        return default
    return _env_default(name)


def values() -> dict[str, Any]:
    return {field.name: get(field.name) for field in FIELDS}


def describe(group: str | None = None, *, onboarding: bool | None = None) -> list[dict[str, Any]]:
    """Fields ready for a template (current value, label, help, options)."""
    described: list[dict[str, Any]] = []
    for field in FIELDS:
        if group is not None and field.group != group:
            continue
        if onboarding is True and not field.onboarding:
            continue
        described.append(
            {
                "name": field.name,
                "label": field.label,
                "help": field.help,
                "kind": field.kind,
                "choices": field.choices,
                "minimum": field.minimum,
                "maximum": field.maximum,
                "secret": field.secret,
                # Segredo já guardado? Só o fato, nunca o valor (nem o hash).
                "defined": bool(get(field.name)) if field.secret else False,
                "advanced": field.advanced,
                "group": field.group,
                "value": "" if field.secret else get(field.name),
            }
        )
    return described


def groups_for(onboarding: bool = True) -> list[tuple[str, str]]:
    """Group ids/labels that actually have fields for this screen."""
    seen: list[tuple[str, str]] = []
    for field in FIELDS:
        if onboarding and not field.onboarding:
            continue
        if field.group not in [key for key, _ in seen]:
            seen.append((field.group, GROUPS.get(field.group, field.group)))
    return seen


# --- escrita --------------------------------------------------------------
def field_names(*, onboarding: bool = False) -> set[str]:
    """Nomes dos campos que uma tela renderiza (o assistente mostra menos)."""
    if onboarding:
        return {field.name for field in FIELDS if field.onboarding}
    return {field.name for field in FIELDS}


def save(
    session: Session,
    data: Mapping[str, Any],
    *,
    rendered: set[str] | None = None,
) -> list[str]:
    """Store the submitted fields, validated; returns user-facing warnings.

    ``rendered`` lists the fields the form actually showed. A boolean that was
    rendered but is missing from the payload is an unchecked box, so it becomes
    False. Fields that were **not** on the screen are left untouched -- sem isso,
    salvar um formulário parcial (o assistente mostra menos campos) apagaria
    configurações que o usuário nem viu.
    """
    visiveis = rendered or set()
    warnings: list[str] = []
    for field in FIELDS:
        present = field.name in data
        if not present and not (field.kind == "bool" and field.name in visiveis):
            continue
        if field.kind == "password":
            text = str(data.get(field.name) or "").strip()
            if not text:
                continue  # mantém o que já existe
            if field.minimum is not None and len(text) < field.minimum:
                warnings.append(
                    f"{field.label}: mínimo de {int(field.minimum)} caracteres."
                )
                continue
            _store(session, field, passwords.hash_password(text))
            continue
        value, warning = _validate(field, data.get(field.name))
        if warning:
            warnings.append(warning)
        _store(session, field, value)
    session.commit()
    load(session)
    return warnings


def _validate(field: Field, raw: Any) -> tuple[Any, str | None]:
    if field.kind == "bool":
        return _as_bool(raw), None
    if field.kind == "choice":
        value = str(raw or "").strip().lower()
        allowed = [choice for choice, _label in field.choices]
        if value not in allowed:
            return allowed[0], f"{field.label}: valor inválido, mantido «{allowed[0]}»."
        return value, None
    if field.kind in {"int", "float"}:
        caster = int if field.kind == "int" else float
        try:
            number = caster(str(raw).strip() or 0)
        except (TypeError, ValueError):
            return _env_default(field.name), f"{field.label}: número inválido, mantido o anterior."
        if field.minimum is not None and number < field.minimum:
            number = caster(field.minimum)
        if field.maximum is not None and number > field.maximum:
            number = caster(field.maximum)
        return number, None
    return str(raw or "").strip(), None


def _store(session: Session, field: Field, value: Any) -> None:
    settings_store.set_value(session, field.store_key, _to_storage(value))
    _cache[field.name] = value


def _env_default(name: str) -> Any:
    """Value from ``.env``/defaults when the database has nothing to say."""
    field = _BY_NAME.get(name)
    if field is not None and field.secret:
        return None
    # get_base_settings (e não get_settings) para não chamar o overlay de volta.
    value = getattr(get_base_settings(), name, None)
    if value is None and field is not None:
        return field.default
    return value


def _as_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "on", "yes", "sim"}


def _to_storage(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _from_storage(field: Field, raw: str) -> Any:
    if field.kind == "bool":
        return _as_bool(raw)
    if field.kind in {"int", "float"}:
        caster = int if field.kind == "int" else float
        try:
            return caster(raw)
        except (TypeError, ValueError):
            return None
    return raw
