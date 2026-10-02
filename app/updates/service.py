"""Self-update monitoring: notice, details and one-click update.

State lives in the `settings` table (no migration needed) and is mirrored into
an in-memory snapshot, so rendering the banner costs no subprocess and no
session. Network-heavy work only runs from the maintenance tick, the manual
check button, or the apply button — always off the request path.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.security import settings_store
from app.updates import git
from app.updates.git import UpdateError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Canonical repository (used when the checkout has no `origin` remote).
DEFAULT_REPO = "https://github.com/MarcosViniicius/eink-opds-server.git"
DEFAULT_BRANCH = "main"
DEFAULT_INTERVAL_HOURS = 6

#: Commits kept for the details view (newest first).
MAX_COMMITS_STORED = 20

_KEY_LAST_CHECK = "update_last_check"
_KEY_AVAILABLE = "update_available"
_KEY_REMOTE_SHA = "update_remote_sha"
_KEY_LOCAL_SHA = "update_local_sha"
_KEY_BEHIND = "update_behind"
_KEY_COMMITS = "update_commits"
_KEY_ERROR = "update_error"
_KEY_RESULT = "update_last_result"
_KEY_RESULT_AT = "update_last_result_at"

_SNAPSHOT: dict[str, Any] = {"available": False, "behind": 0}


def snapshot() -> dict[str, Any]:
    """In-memory status for template rendering (never touches disk/network)."""
    return dict(_SNAPSHOT)


def _publish(available: bool, behind: int) -> None:
    global _SNAPSHOT
    _SNAPSHOT = {"available": bool(available), "behind": max(0, int(behind or 0))}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None
    if moment is not None and moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def resolve_config(settings) -> tuple[str, str, int]:
    """(repo_url, branch, interval_hours) from configuration.

    `repo_url` falls back to the checkout's `origin` remote and then to the
    canonical repository. Raises `UpdateError` on invalid user input.
    """
    branch = (getattr(settings, "update_branch", "") or DEFAULT_BRANCH).strip()
    if not git.valid_branch(branch):
        raise UpdateError("Branch inválida: use letras, números, ponto, _ / -.")
    configured = (getattr(settings, "update_repo_url", "") or "").strip()
    if configured and not git.valid_repo_url(configured):
        raise UpdateError("URL do repositório inválida: use https:// (ou file:// local).")
    repo_url = configured or DEFAULT_REPO
    try:
        interval = int(getattr(settings, "update_interval_hours", 0) or 0)
    except (TypeError, ValueError):
        interval = 0
    if interval <= 0:
        interval = DEFAULT_INTERVAL_HOURS
    return repo_url, branch, min(168, max(1, interval))


def preflight(root=None) -> tuple[bool, str | None]:
    """Cheap capability check for routes (no session, no network)."""
    if git.git_binary() is None:
        return False, "O git não está instalado neste servidor — atualize manualmente."
    root = git.project_root() if root is None else root
    if root is None:
        return False, (
            "Esta instalação não é um checkout git (ex.: imagem Docker, que não "
            "leva o repositório). Atualize no host: git pull e recrie os containers."
        )
    return True, None


def _read_status(session: Session) -> dict[str, Any]:
    get = lambda key, default=None: settings_store.get(session, key, default)  # noqa: E731
    try:
        behind = int(get(_KEY_BEHIND, "0") or 0)
    except (TypeError, ValueError):
        behind = 0
    try:
        commits = json.loads(get(_KEY_COMMITS, "[]") or "[]")
        if not isinstance(commits, list):
            commits = []
    except (TypeError, ValueError):
        commits = []
    return {
        "available": get(_KEY_AVAILABLE, "0") == "1",
        "behind": max(0, behind),
        "local_sha": get(_KEY_LOCAL_SHA),
        "remote_sha": get(_KEY_REMOTE_SHA),
        "checked_at": _parse_iso(get(_KEY_LAST_CHECK)),
        "error": get(_KEY_ERROR) or None,
        "commits": commits,
        "result": get(_KEY_RESULT) or None,
        "result_at": _parse_iso(get(_KEY_RESULT_AT)),
    }


def _persist(session: Session, status: dict[str, Any]) -> None:
    settings_store.set_value(session, _KEY_AVAILABLE, "1" if status["available"] else "0")
    settings_store.set_value(session, _KEY_BEHIND, str(status["behind"]))
    settings_store.set_value(session, _KEY_REMOTE_SHA, status["remote_sha"] or "")
    settings_store.set_value(session, _KEY_LOCAL_SHA, status["local_sha"] or "")
    settings_store.set_value(
        session, _KEY_COMMITS, json.dumps(status["commits"][:MAX_COMMITS_STORED], ensure_ascii=False)
    )
    settings_store.set_value(session, _KEY_ERROR, status["error"] or "")
    settings_store.set_value(session, _KEY_LAST_CHECK, _now_iso())
    session.commit()
    _publish(status["available"], status["behind"])


def _due(session: Session, interval_hours: int) -> bool:
    last = _parse_iso(settings_store.get(session, _KEY_LAST_CHECK))
    if last is None:
        return True
    elapsed = (datetime.now(UTC) - last).total_seconds()
    return elapsed >= interval_hours * 3600


def maybe_check(session: Session, settings, *, force: bool = False, root=None) -> dict[str, Any]:
    """Compare the checkout with the remote, throttled by the interval.

    Never raises: failures are recorded on the status (`error`) so the panel
    can explain them instead of showing a crash.
    """
    root = git.project_root() if root is None else root
    status = _read_status(session)
    if root is None or git.git_binary() is None:
        _publish(status["available"], status["behind"])
        return status
    if not bool(getattr(settings, "update_check_enabled", True)) and not force:
        _publish(status["available"], status["behind"])
        return status
    try:
        repo_url, branch, interval = resolve_config(settings)
    except UpdateError as exc:
        status["error"] = str(exc)
        _persist(session, status)
        return status
    if not force and not _due(session, interval):
        _publish(status["available"], status["behind"])
        return status
    try:
        local = git.local_head(root)
        if not local:
            raise UpdateError("Não consegui ler o commit local (repositório vazio?).")
        status["local_sha"] = local
        remote = git.remote_head(root, repo_url, branch)
        if not remote:
            raise UpdateError(f"A branch '{branch}' não existe no repositório remoto.")
        status["remote_sha"] = remote
        if remote == local:
            status.update(available=False, behind=0, commits=[], error=None)
        else:
            git.fetch_branch(root, repo_url, branch)
            behind = git.behind_count(root)
            commits = git.parse_log(git.read_log(root, MAX_COMMITS_STORED))[:MAX_COMMITS_STORED]
            status.update(available=True, behind=behind, commits=commits, error=None)
    except UpdateError as exc:
        logger.info("update check failed", extra={"error": str(exc)})
        status["error"] = str(exc)
    _persist(session, status)
    return status


def details(session: Session) -> dict[str, Any]:
    """Everything the settings section needs (no network here)."""
    from app.updates import git as _git

    root = _git.project_root()
    status = _read_status(session)
    capable, reason = preflight(root)
    local = _git.local_head(root) if root and _git.git_binary() else None
    for commit in status["commits"]:
        commit["date"] = _parse_iso(commit.get("date"))
    return {
        **status,
        "capable": capable,
        "reason": reason,
        "in_docker": _git.in_docker(),
        "live_local_sha": local,
        "live_local_short": (local or "")[:7] or None,
    }


def _save_result(session: Session, message: str) -> None:
    settings_store.set_value(session, _KEY_RESULT, message[:2000])
    settings_store.set_value(session, _KEY_RESULT_AT, _now_iso())
    session.commit()


def check_updates_in_background() -> None:
    """Manual "verify now" worker (own session; never crashes the loop)."""
    from app.config import get_settings
    from app.database import session_scope

    try:
        with session_scope() as session:
            report = maybe_check(session, get_settings(), force=True)
        logger.info("manual update check finished", extra={"report": _public(report)})
    except Exception:  # noqa: BLE001 - background task: never crash the loop
        logger.exception("background update check failed")


def _public(status: dict[str, Any]) -> dict[str, Any]:
    return {k: status.get(k) for k in ("available", "behind", "error")}


def apply_update_in_background(
    *,
    root=None,
    repo_url: str | None = None,
    branch: str | None = None,
    in_docker: bool | None = None,
    settings=None,
) -> None:
    """Pull the remote branch and, on Docker, rebuild the stack.

    Runs in a worker thread with its own session. The outcome (always
    human-readable) is persisted for the settings section to show. `root`,
    `repo_url`, `branch` and `in_docker` default to the live installation and
    exist so tests can point at throwaway clones instead of the real checkout.
    """
    from app.config import get_settings
    from app.database import session_scope

    root = git.project_root() if root is None else root
    try:
        with session_scope() as session:
            if settings is None:
                settings = get_settings()
            ok, reason = preflight(root)
            if not ok:
                _save_result(session, f"Não foi possível atualizar: {reason}")
                return
            assert root is not None
            if repo_url is None or branch is None:
                try:
                    repo_url, branch, _interval = resolve_config(settings)
                except UpdateError as exc:
                    _save_result(session, f"Não foi possível atualizar: {exc}")
                    return
            if in_docker is None:
                in_docker = git.in_docker()
            current = git.current_branch(root)
            if current != branch:
                _save_result(
                    session,
                    f"Não foi possível atualizar: este checkout está na branch "
                    f"'{current or '?'}', e a monitorada é '{branch}'. Troque de "
                    f"branch manualmente e tente de novo.",
                )
                return
            if not git.is_clean(root):
                _save_result(
                    session,
                    "Não foi possível atualizar: há alterações locais não salvas "
                    "(git status). Guarde ou desfaça as alterações e tente de novo.",
                )
                return
            try:
                git.pull_ff(root, repo_url, branch)
            except UpdateError as exc:
                _save_result(session, f"A atualização falhou: {exc}")
                return
            # Recompute the truth after pulling (usually: nothing behind now).
            try:
                maybe_check(session, settings, force=True, root=root)
            except Exception:  # noqa: BLE001 - the pull already succeeded
                logger.exception("post-pull update check failed")
            message = "Código atualizado com sucesso."
            if in_docker:
                compose_yml = git.compose_file(root)
                if compose_yml is not None and git.docker_binary() is not None:
                    try:
                        git.compose_rebuild(compose_yml)
                        message += " Containers reconstruídos — o servidor já está rodando o código novo."
                    except UpdateError as exc:
                        message += (
                            f" Código atualizado, mas a reconstrução falhou: {exc} "
                            f"Suba os containers manualmente: docker compose up -d --build."
                        )
                else:
                    message += (
                        " Código atualizado, mas não encontrei o docker/compose aqui: "
                        "suba os containers manualmente (docker compose up -d --build)."
                    )
            else:
                message += " Reinicie o servidor para usar o código novo."
            _save_result(session, message)
            logger.info("update applied", extra={"branch": branch})
    except Exception:  # noqa: BLE001 - background task: never crash the loop
        logger.exception("background update apply failed")
