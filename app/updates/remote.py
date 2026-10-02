"""Remote comparison without git, for installs running from the Docker image.

The image ships neither a ``.git`` directory nor the git binary, so the only
link to "what is installed" is the commit recorded at build time
(``INKPORT_COMMIT``, fed by the ``GIT_SHA`` build arg). With it, the GitHub API
answers what the branch has that this install does not.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

import httpx

from app.updates.git import UpdateError

logger = logging.getLogger(__name__)

USER_AGENT = "inkport-updater"
API_TIMEOUT = 20.0

_SLUG_RE = re.compile(r"github\.com[/:](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


def github_slug(repo_url: str | None) -> str | None:
    """``owner/repo`` for a GitHub URL (https, ssh or with .git), else None."""
    match = _SLUG_RE.search((repo_url or "").strip())
    if not match:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"


def commit_rows(items) -> list[dict]:
    """Map the API commit list to the shape the panel uses."""
    rows: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        commit = item.get("commit") or {}
        message = (commit.get("message") or "").strip()
        subject, _, body = message.partition("\n")
        sha = str(item.get("sha") or "")
        author = commit.get("author") or {}
        rows.append(
            {
                "sha": sha,
                "short": sha[:7],
                "author": author.get("name") or "—",
                "date": author.get("date"),
                "subject": subject.strip() or "(sem mensagem)",
                "body": body.strip(),
            }
        )
    return rows


def compare_github(
    repo_url: str | None, branch: str, base_sha: str, *, timeout: float = API_TIMEOUT
) -> dict:
    """What ``branch`` has that ``base_sha`` does not, via the GitHub API."""
    slug = github_slug(repo_url)
    if slug is None:
        raise UpdateError(
            "Sem git, só consigo verificar repositórios do GitHub. Aponte a URL "
            "do repositório nas configurações."
        )
    url = f"https://api.github.com/repos/{slug}/compare/{quote(base_sha)}...{quote(branch)}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    try:
        with httpx.Client(timeout=timeout, headers=headers) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise UpdateError(f"Não consegui falar com o GitHub: {exc}.") from exc

    if response.status_code == 404:
        raise UpdateError("O repositório, a branch ou o commit de build não existem no GitHub.")
    if response.status_code == 403:
        raise UpdateError("O GitHub recusou a consulta (limite de uso) — tente mais tarde.")
    if response.status_code >= 400:
        raise UpdateError(f"O GitHub respondeu {response.status_code}.")
    try:
        data = response.json()
    except ValueError as exc:
        raise UpdateError("Resposta inesperada do GitHub.") from exc

    status = str(data.get("status") or "")
    commits = commit_rows(data.get("commits"))
    if status == "identical" or not commits:
        return {
            "available": False,
            "behind": 0,
            "remote_sha": base_sha,
            "commits": [],
            "warning": None,
        }
    warning = None
    if status == "diverged":
        warning = (
            "O histórico divergiu do remoto: dá para ver o que mudou, mas a "
            "atualização precisa ser feita manualmente (git pull)."
        )
    return {
        "available": True,
        "behind": int(data.get("ahead_by") or len(commits)),
        "remote_sha": commits[-1]["sha"],
        "commits": commits,
        "warning": warning,
    }
