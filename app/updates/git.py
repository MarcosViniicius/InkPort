"""Git plumbing for self-updates.

Every command runs through :func:`run_git` (fixed argv, no shell, timeouts) and
raises :class:`UpdateError` with a user-facing message on failure. Nothing here
touches local branches: the remote is only read (`ls-remote`, `fetch` into
`FETCH_HEAD`) until the user explicitly applies an update.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

#: Image build arg (`--build-arg GIT_SHA=…`) recorded as an env var.
BUILD_SHA_ENV = "INKPORT_COMMIT"
_BUILD_SHA_RE = re.compile(r"[0-9a-fA-F]{7,40}")


class UpdateError(RuntimeError):
    """Something the user can act on (shown in the panel)."""


#: Network budgets. `ls-remote` is one HTTPS round-trip; fetch/pull move data.
NET_TIMEOUT = 30
FETCH_TIMEOUT = 180
PULL_TIMEOUT = 900
COMPOSE_TIMEOUT = 1800

#: What a branch name may look like (also blocks shell metacharacters).
_BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]{1,100}")

#: `git log` field/record separators (bodies may contain newlines).
LOG_FORMAT = "%H%x1f%h%x1f%an%x1f%aI%x1f%s%x1f%b%x1e"


@functools.lru_cache(maxsize=1)
def project_root() -> Path | None:
    """Nearest ancestor of this file containing ``.git`` (the panel checkout)."""
    for parent in Path(__file__).resolve().parents:
        try:
            if (parent / ".git").is_dir():
                return parent
        except OSError:
            continue
    return None


def git_binary() -> str | None:
    """Path to git, if installed."""
    return shutil.which("git")


def build_commit() -> str | None:
    """Commit recorded at image build time, when the image carries one."""
    value = (os.environ.get(BUILD_SHA_ENV) or "").strip()
    return value if _BUILD_SHA_RE.fullmatch(value) else None


def in_docker() -> bool:
    """Best-effort container detection (never raises)."""
    try:
        if Path("/.dockerenv").exists():
            return True
    except OSError:
        return False
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "docker" in cgroup or "kubepods" in cgroup


def valid_branch(name: str | None) -> bool:
    """Branch names accepted from configuration (no metacharacters)."""
    return bool(name and _BRANCH_RE.fullmatch(name.strip()))


def valid_repo_url(url: str | None) -> bool:
    """Remote URLs accepted from configuration.

    `https://` for the real repository; `file://` with an absolute path for
    local mirrors (and deterministic tests).
    """
    from urllib.parse import urlparse

    text = (url or "").strip()
    if len(text) >= 500:
        return False
    if text.startswith("https://"):
        return True
    if not text.startswith("file://"):
        return False
    parsed = urlparse(text)
    if parsed.netloc not in ("", "localhost"):
        return False
    path = parsed.path
    import os

    if os.name == "nt" and re.fullmatch(r"/[A-Za-z]:/.*", path):
        return True
    try:
        return Path(path).is_absolute()
    except (OSError, ValueError):
        return False


def run_git(root: Path, *args: str, timeout: int) -> str:
    """Run one git command in `root`; return stdout or raise `UpdateError`."""
    exe = git_binary()
    if exe is None:
        raise UpdateError("O git não está instalado neste servidor.")
    try:
        proc = subprocess.run(
            [exe, "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(f"O git demorou demais ({' '.join(args[:2])}).") from exc
    except OSError as exc:
        raise UpdateError(f"Não consegui rodar o git: {exc}.") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1][:300] if detail else "sem detalhes"
        raise UpdateError(f"git {' '.join(args[:3])} falhou: {tail}.")
    return proc.stdout


def local_head(root: Path) -> str | None:
    """Full SHA of the local checkout, if it has commits."""
    try:
        return run_git(root, "rev-parse", "HEAD", timeout=NET_TIMEOUT).strip() or None
    except UpdateError:
        return None


def current_branch(root: Path) -> str | None:
    """Checked-out branch, or None when detached/unknown."""
    try:
        name = run_git(root, "branch", "--show-current", timeout=NET_TIMEOUT).strip()
    except UpdateError:
        return None
    return name or None


def is_clean(root: Path) -> bool:
    """True when there are no local modifications to lose."""
    return run_git(root, "status", "--porcelain", timeout=NET_TIMEOUT).strip() == ""


def origin_remote(root: Path) -> str | None:
    """URL of the `origin` remote, if configured."""
    try:
        url = run_git(root, "remote", "get-url", "origin", timeout=NET_TIMEOUT).strip()
    except UpdateError:
        return None
    return url or None


def parse_ls_remote(output: str) -> str | None:
    """First SHA of `git ls-remote` output (empty when the branch is missing)."""
    for line in (output or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]{40}", parts[0]):
            return parts[0]
    return None


def remote_head(root: Path, repo_url: str, branch: str) -> str | None:
    """SHA of `branch` on the remote (network; None when the branch is missing)."""
    out = run_git(root, "ls-remote", repo_url, f"refs/heads/{branch}", timeout=NET_TIMEOUT)
    return parse_ls_remote(out)


def fetch_branch(root: Path, repo_url: str, branch: str) -> None:
    """Fetch `branch` into FETCH_HEAD without touching local branches."""
    run_git(root, "fetch", "--quiet", repo_url, branch, timeout=FETCH_TIMEOUT)


def behind_count(root: Path) -> int:
    """Commits on FETCH_HEAD that HEAD does not have."""
    out = run_git(root, "rev-list", "--count", "HEAD..FETCH_HEAD", timeout=NET_TIMEOUT)
    try:
        return max(0, int(out.strip()))
    except ValueError:
        return 0


def read_log(root: Path, limit: int = 50) -> str:
    """Raw log of the commits HEAD is missing (newest first)."""
    return run_git(
        root, "log", f"--format={LOG_FORMAT}", f"-n{max(1, limit)}",
        "HEAD..FETCH_HEAD", timeout=NET_TIMEOUT,
    )


def parse_log(output: str) -> list[dict]:
    """Parse :data:`LOG_FORMAT` output into commit dicts."""
    commits: list[dict] = []
    for record in (output or "").split("\x1e"):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split("\x1f")
        if len(parts) < 6:
            logger.warning("skipping malformed log record", extra={"parts": len(parts)})
            continue
        sha, short, author, date, subject = (p.strip() for p in parts[:5])
        body = parts[5].strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            continue
        commits.append(
            {
                "sha": sha,
                "short": short or sha[:7],
                "author": author or "—",
                "date": date,
                "subject": subject or "(sem mensagem)",
                "body": body,
            }
        )
    return commits


def pull_ff(root: Path, repo_url: str, branch: str) -> str:
    """Fast-forward the checkout to the remote branch; refuses anything else."""
    return run_git(root, "pull", "--ff-only", repo_url, branch, timeout=PULL_TIMEOUT)


def compose_file(root: Path) -> Path | None:
    """The compose file driving this checkout, if any."""
    for name in ("docker-compose.yml", "compose.yml", "compose.yaml"):
        candidate = root / name
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def docker_binary() -> str | None:
    """Path to docker, if installed."""
    return shutil.which("docker")


def compose_rebuild(compose_yml: Path) -> str:
    """Rebuild and restart the stack described by `compose_yml`."""
    exe = docker_binary()
    if exe is None:
        raise UpdateError("O docker não está instalado neste servidor.")
    try:
        proc = subprocess.run(
            [exe, "compose", "-f", str(compose_yml), "up", "-d", "--build"],
            capture_output=True,
            text=True,
            timeout=COMPOSE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("O rebuild do docker demorou demais.") from exc
    except OSError as exc:
        raise UpdateError(f"Não consegui rodar o docker: {exc}.") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = tail[-1][:300] if tail else "sem detalhes"
        raise UpdateError(f"docker compose falhou: {detail}.")
    return proc.stdout.strip()[-2000:]
