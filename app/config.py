"""Application configuration, loaded from ``.env`` and the environment.

Kept deliberately dependency-light: ``pydantic-settings`` gives us typed,
validated settings with ``.env`` support and nothing else is needed.
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from app.security.effective import EffectiveSettings

#: Segredo de sessão de fábrica: público, portanto nunca aceitável em produção.
DEFAULT_SECRET_KEY = "change-me-please-use-a-long-random-string"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App -----------------------------------------------------------------
    app_name: str = "InkPort"
    debug: bool = False

    # Interface to bind. "0.0.0.0" = every interface on the network.
    host: str = "0.0.0.0"
    port: int = 8080
    # Public base URL used to build absolute OPDS/RSS links.
    # Empty = auto: uses the LAN IP when bound to 0.0.0.0, else localhost.
    # Set it explicitly when behind a reverse proxy or using Docker.
    base_url: str = ""
    # Preferir o endereço que o cliente usou (cabeçalho Host) ao montar links.
    # É o que faz o catálogo funcionar por Tailscale, VPN, IP da LAN ou domínio.
    # Desligue (false) se precisar anunciar sempre a BASE_URL (proxy reverso).
    use_request_host: bool = True
    # Trust X-Forwarded-* headers (enable only behind a reverse proxy you own).
    trust_proxy: bool = False

    # --- Storage -------------------------------------------------------------
    data_dir: Path = Path("./data")
    max_upload_mb: int = 2048
    storage_limit_gb: float = 0.0
    # Cifragem do banco (SQLCipher). "auto" = cifrado quando o pacote sqlcipher3
    # existe; "off" = texto puro (escape hatch para plataformas sem roda pronta).
    # A chave fica em DATA_DIR/secret.key e NÃO deve ser perdida.
    db_encryption: str = "auto"

    # --- Security ------------------------------------------------------------
    secret_key: str = "change-me-please-use-a-long-random-string"
    admin_username: str = "admin"
    admin_password: str = "admin"
    opds_username: str = ""
    opds_password: str = ""
    require_auth_panel: bool = True

    # --- OPDS catalog --------------------------------------------------------
    # What the catalog root ("/opds") shows:
    #   mixed      -> recent books *and* the sections (works on every client,
    #                 including CrossPoint, which only lists acquisition entries);
    #   navigation -> only the sections, no books (for clients that show entries
    #                 and prefer to "travel" through the categories);
    #   books      -> only the books, no sections.
    # "/opds/browse" always offers the pure navigation feed.
    opds_root_mode: str = "mixed"

    @field_validator("opds_root_mode", mode="after")
    @classmethod
    def _valid_root_mode(cls, value: str) -> str:
        allowed = {"mixed", "navigation", "books"}
        cleaned = (value or "mixed").strip().lower()
        if cleaned not in allowed:
            raise ValueError(f"OPDS_ROOT_MODE deve ser um de: {', '.join(sorted(allowed))}")
        return cleaned

    # --- Workers -------------------------------------------------------------
    conversion_concurrency: int = 2
    conversion_timeout: int = 1800
    worker_poll_interval: float = 2.0
    rss_worker_enabled: bool = True

    # --- Logging -------------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = False

    # --- External tools (empty = auto-detect on PATH) ------------------------
    calibre_ebook_convert: str = ""
    imagemagick_bin: str = ""
    ghostscript_bin: str = ""
    ffmpeg_bin: str = ""

    # ---------------------------------------------------------------------
    # Derived paths. Computed once in a validator so every module can rely
    # on them being absolute and consistent.
    # ---------------------------------------------------------------------
    @field_validator("data_dir", mode="after")
    @classmethod
    def _abs_data_dir(cls, v: Path) -> Path:
        return v.expanduser().resolve()

    @property
    def library_dir(self) -> Path:
        return self.data_dir / "library"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "temp"

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "opds.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def all_dirs(self) -> tuple[Path, ...]:
        return (
            self.data_dir,
            self.library_dir,
            self.inbox_dir,
            self.temp_dir,
            self.covers_dir,
            self.logs_dir,
        )

    def ensure_dirs(self) -> None:
        for path in self.all_dirs:
            path.mkdir(parents=True, exist_ok=True)

    @property
    def base_url_clean(self) -> str:
        from app.networking import resolve_base_url

        return resolve_base_url(self.base_url, self.host, self.port)

    @property
    def access_urls(self) -> list[str]:
        """All URLs where the panel/OPDS can be reached on the network."""
        from app.networking import access_urls

        return access_urls(self.host, self.port)

    @property
    def on_network(self) -> bool:
        from app.networking import is_wildcard

        return is_wildcard(self.host)


@lru_cache
def get_base_settings() -> Settings:
    """Valores do ``.env`` (bootstrap: caminhos, host, porta, log, cifragem)."""
    settings = Settings()
    settings.ensure_dirs()
    _ensure_session_secret(settings)
    return settings


def _ensure_session_secret(settings: Settings) -> None:
    """Troca o segredo de sessão de fábrica por um aleatório persistido.

    O valor padrão é público (está no ``.env.example``): aceitá-lo permitiria a
    qualquer um forjar o cookie de sessão do painel. Sem ``SECRET_KEY`` definido,
    geramos um em ``DATA_DIR/session.key`` (0600) -- como a chave do banco -- e
    avisamos no log. Definir ``SECRET_KEY`` continua mandando.
    """
    if settings.secret_key and settings.secret_key != DEFAULT_SECRET_KEY:
        return
    path = settings.data_dir / "session.key"
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
    else:
        value = secrets.token_urlsafe(48)
        descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        logging.getLogger(__name__).warning(
            "SECRET_KEY não definido: gerei um segredo de sessão em %s "
            "(defina SECRET_KEY no ambiente para controlar isso)",
            path,
        )
    settings.secret_key = value


@lru_cache
def get_settings() -> EffectiveSettings:
    """Configuração efetiva: o que está no banco por cima do ``.env``.

    Todo o app lê daqui. O ``.env`` continua servindo de bootstrap e de padrão
    para o que ainda não foi configurado no painel (``security/runtime.py``).
    """
    from app.security.effective import EffectiveSettings

    return EffectiveSettings(get_base_settings())
