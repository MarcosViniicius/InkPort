"""Self-update monitoring: parsing, validation and check/apply.

Unit tests cover the pure helpers; integration tests build throwaway bare
origin + clone repos (skipped when git is missing) and drive the service
against `file://` URLs, so no network is needed. The apply route itself is not
exercised over HTTP on purpose: it would pull into the real project checkout.

Run with:  python tests/update.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="opds_upd_"))
os.chdir(WORKDIR)  # sem .env por perto: instalação nova de verdade
os.environ["DATA_DIR"] = str(WORKDIR)
os.environ["SECRET_KEY"] = "update-test-secret"
os.environ["RSS_WORKER_ENABLED"] = "false"

PASSED: list[str] = []
FAILED: list[str] = []

GIT = shutil.which("git")


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr[:200]}"
    return proc.stdout


def make_repos(tag: str, commits: int = 2) -> tuple[str, Path]:
    """Bare origin + clone; returns (file:// url, clone path)."""
    base = WORKDIR / f"repos_{tag}"
    origin = base / "origin.git"
    origin.parent.mkdir(parents=True, exist_ok=True)
    git("init", "--bare", "-b", "main", str(origin), cwd=WORKDIR)
    clone = base / "clone"
    git("clone", str(origin), str(clone), cwd=WORKDIR)
    git("config", "user.email", "teste@exemplo", cwd=clone)
    git("config", "user.name", "Teste", cwd=clone)
    for i in range(1, commits + 1):
        (clone / f"arq{i}.txt").write_text(f"conteúdo {i}\n", encoding="utf-8")
        git("add", ".", cwd=clone)
        git(
            "-c", "user.email=teste@exemplo", "-c", "user.name=Teste",
            "commit", "-m", f"Commit número {i}",
            cwd=clone,
        )
    git("push", "origin", "main", cwd=clone)
    return origin.as_uri(), clone


def advance_origin(origin_url: str, tag: str, commits: int = 2) -> None:
    """Push new commits to the bare origin through a throwaway writer clone."""
    writer = WORKDIR / f"repos_{tag}_writer"
    git("clone", origin_url, str(writer), cwd=WORKDIR)
    git("config", "user.email", "teste@exemplo", cwd=writer)
    git("config", "user.name", "Teste", cwd=writer)
    for i in range(1, commits + 1):
        (writer / f"novo{i}.txt").write_text(f"novo {i}\n", encoding="utf-8")
        git("add", ".", cwd=writer)
        git(
            "-c", "user.email=teste@exemplo", "-c", "user.name=Teste",
            "commit", "-m", f"Novidade {i}",
            cwd=writer,
        )
    git("push", "origin", "main", cwd=writer)


def main() -> int:
    from app.updates import git as gitmod
    from app.updates import service

    print("\n[validação de entradas]")
    check("branch simples vale", gitmod.valid_branch("main"))
    check("branch com barra vale", gitmod.valid_branch("release/1.0"))
    check("branch vazia não vale", not gitmod.valid_branch(""))
    check("branch com espaço não vale", not gitmod.valid_branch("minha branch"))
    check("branch com ; não vale (injeção)", not gitmod.valid_branch("main; rm -rf /"))
    check("branch gigante não vale", not gitmod.valid_branch("a" * 101))
    check("https vale", gitmod.valid_repo_url("https://github.com/a/b.git"))
    check("http não vale", not gitmod.valid_repo_url("http://github.com/a/b.git"))
    check("ssh não vale", not gitmod.valid_repo_url("git@github.com:a/b.git"))
    check("file:// absoluto vale", gitmod.valid_repo_url(WORKDIR.as_uri() + "/x.git"))
    check("file:// relativo não vale", not gitmod.valid_repo_url("file://relativo/x.git"))

    print("\n[caminho sem git (imagem Docker): slug, build e API]")
    from app.updates import remote as remote_mod

    check("slug de https com .git", remote_mod.github_slug("https://github.com/a/b.git") == "a/b")
    check("slug de https sem .git", remote_mod.github_slug("https://github.com/a/b") == "a/b")
    check("slug de ssh", remote_mod.github_slug("git@github.com:a/b.git") == "a/b")
    check("slug de repositório não-GitHub vira None", remote_mod.github_slug("https://gitlab.com/a/b") is None)
    check("slug vazio vira None", remote_mod.github_slug("") is None)

    os.environ.pop("INKPORT_COMMIT", None)
    check("sem build arg não inventa commit", gitmod.build_commit() is None)
    os.environ["INKPORT_COMMIT"] = "lixo"
    check("build arg inválido é ignorado", gitmod.build_commit() is None)
    os.environ["INKPORT_COMMIT"] = "af08972e1a5babbaeb4d7604ce5c23938aed0d8c"
    check("build arg com SHA vale", gitmod.build_commit() == "af08972e1a5babbaeb4d7604ce5c23938aed0d8c")
    check("SHA curto também vale", gitmod.build_commit() is not None)

    rows = remote_mod.commit_rows(
        [
            {
                "sha": "b" * 40,
                "commit": {
                    "message": "Título do commit\n\nCorpo com detalhe",
                    "author": {"name": "Autora", "date": "2026-10-01T10:00:00Z"},
                },
            },
            {"sha": "c" * 40, "commit": {"message": "Só título"}},
        ]
    )
    check("API: mapeia 2 commits", len(rows) == 2, str(len(rows)))
    check("API: assunto e corpo separados", rows[0]["subject"] == "Título do commit" and rows[0]["body"] == "Corpo com detalhe")
    check("API: autor e data", rows[0]["author"] == "Autora" and rows[0]["date"] == "2026-10-01T10:00:00Z")
    check("API: sem autor vira travessão", rows[1]["author"] == "—", rows[1]["author"])

    print("\n[parse de ls-remote e log]")
    check(
        "ls-remote extrai o SHA",
        gitmod.parse_ls_remote("abc123" + "4" * 34 + "\trefs/heads/main\n") == "abc123" + "4" * 34,
    )
    check("ls-remote vazio vira None", gitmod.parse_ls_remote("") is None)
    check("ls-remote com lixo vira None", gitmod.parse_ls_remote("nada aqui\n") is None)
    sha1, sha2 = "a" * 40, "b" * 40
    raw = (
        f"{sha1}\x1fabc1234\x1fAutora\x1f2026-09-30T10:00:00+00:00\x1fAssunto um\x1fCorpo um\nlinha dois\x1e"
        f"{sha2}\x1fdef5678\x1fAutor\x1f2026-09-29T10:00:00+00:00\x1fAssunto dois\x1f\x1e"
        "lixo sem separadores\x1e"
    )
    commits = gitmod.parse_log(raw)
    check("log com 2 commits (lixo ignorado)", len(commits) == 2, str(len(commits)))
    check("campos do commit", commits[0]["author"] == "Autora" and commits[0]["short"] == "abc1234")
    check("corpo com quebra de linha", "linha dois" in commits[0]["body"])
    check("corpo vazio vira string", commits[1]["body"] == "")

    print("\n[snapshot inicial]")
    snap = service.snapshot()
    check("sem aviso antes de qualquer verificação", snap == {"available": False, "behind": 0}, str(snap))

    if GIT is None:
        print("\nsem git instalado: integração pulada")
    else:
        _integration(service)
        _docker_like(service)

    _routes()

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


def _settings(repo_url: str, branch: str = "main"):
    return SimpleNamespace(
        update_check_enabled=True,
        update_branch=branch,
        update_repo_url=repo_url,
        update_interval_hours=6,
    )


def _integration(service) -> None:
    from app.database import init_db, session_scope

    print("\n[check contra repositório local]")
    init_db()
    origin_url, clone = make_repos("base", commits=1)
    advance_origin(origin_url, "base", commits=2)

    with session_scope() as session:
        status = service.maybe_check(
            session, _settings(origin_url), force=True, root=clone
        )
    check("detecta que está atrás", status["available"] is True)
    check("conta os commits (2)", status["behind"] == 2, str(status["behind"]))
    check("detalha os commits", len(status["commits"]) == 2, str(len(status["commits"])))
    check(
        "mensagens dos commits",
        [c["subject"] for c in status["commits"]] == ["Novidade 2", "Novidade 1"],
        str([c["subject"] for c in status["commits"]]),
    )
    snap = service.snapshot()
    check("snapshot acende o aviso", snap["available"] is True and snap["behind"] == 2, str(snap))

    print("\n[em dia não acende aviso]")
    origin_url2, clone2 = make_repos("ok", commits=1)
    with session_scope() as session:
        status = service.maybe_check(
            session, _settings(origin_url2), force=True, root=clone2
        )
    check("nada atrás", status["available"] is False and status["behind"] == 0, str(status))

    print("\n[branch inexistente vira erro legível]")
    with session_scope() as session:
        status = service.maybe_check(
            session, _settings(origin_url2, branch="fantasma"), force=True, root=clone2
        )
    check("erro explica", bool(status["error"]) and "fantasma" in status["error"], str(status["error"]))

    print("\n[apply com árvore suja recusa]")
    (clone / "sujo.txt").write_text("não commitado\n", encoding="utf-8")
    with session_scope() as session:
        before = service.git.local_head(clone)
        service.apply_update_in_background(
            root=clone, repo_url=origin_url, branch="main", in_docker=False,
            settings=_settings(origin_url),
        )
    from app.security import settings_store

    with session_scope() as session:
        result = settings_store.get(session, "update_last_result") or ""
        check("recusa e explica", "alterações locais" in result, result[:120])
        check("nada mudou no checkout", service.git.local_head(clone) == before)
    (clone / "sujo.txt").unlink()

    print("\n[apply puxa e pede reinício (fora do docker)]")
    with session_scope() as session:
        service.apply_update_in_background(
            root=clone, repo_url=origin_url, branch="main", in_docker=False,
            settings=_settings(origin_url),
        )
    with session_scope() as session:
        remote = service.git.remote_head(clone, origin_url, "main")
        check("checkout alcançou o remoto", service.git.local_head(clone) == remote)
        result = settings_store.get(session, "update_last_result") or ""
        check("pede reinício fora do docker", "Reinicie" in result, result[:160])
        status = service.details(session)
        check("depois do pull não há aviso", status["available"] is False, str(status["available"]))


def _docker_like(service) -> None:
    """No checkout + build commit + GitHub API faked: the Docker path."""
    from app.database import session_scope
    from app.security import settings_store
    from app.updates import git as gitmod
    from app.updates import remote as remote_mod

    print("\n[imagem Docker: sem git, com commit de build]")
    sem_git = WORKDIR / "sem_checkout"
    sem_git.mkdir(exist_ok=True)
    caps = service.capabilities(root=sem_git)
    check("sabe verificar", caps["check"] is True, str(caps))
    check("não tenta aplicar daqui", caps["apply"] is False, str(caps["apply"]))

    original = remote_mod.compare_github
    remote_mod.compare_github = lambda repo_url, branch, base, timeout=20.0: {
        "available": True,
        "behind": 2,
        "remote_sha": "f" * 40,
        "commits": [
            {"sha": "e" * 40, "short": "eeeeeee", "author": "Autora",
             "date": "2026-10-01T10:00:00Z", "subject": "Novidade 2", "body": ""},
            {"sha": "f" * 40, "short": "fffffff", "author": "Autor",
             "date": "2026-09-30T10:00:00Z", "subject": "Novidade 1", "body": ""},
        ],
        "warning": None,
    }
    try:
        with session_scope() as session:
            status = service.maybe_check(
                session,
                _settings("https://github.com/exemplo/inkport.git"),
                force=True,
                root=sem_git,
            )
    finally:
        remote_mod.compare_github = original
    check("avisa mesmo sem git", status["available"] is True, str(status))
    check("conta os commits", status["behind"] == 2, str(status["behind"]))
    check("lista os commits", len(status["commits"]) == 2, str(len(status["commits"])))
    check("snapshot acende o aviso", service.snapshot()["available"] is True)
    check(
        "commit local vem do build arg",
        status["local_sha"] == gitmod.build_commit(),
        str(status["local_sha"]),
    )
    service.apply_update_in_background(root=sem_git, in_docker=True)
    with session_scope() as session:
        result = settings_store.get(session, "update_last_result") or ""
    check("aplicar sem checkout é recusado com instrução", "docker compose" in result, result[:140])


def _routes() -> None:
    if GIT is None:
        return
    print("\n[rotas: aviso no painel e verificação manual]")
    from fastapi.testclient import TestClient

    from app.database.base import session_scope
    from app.main import create_app
    from app.security import runtime
    from app.security import setup as setup_state

    origin_url, _clone = make_repos("web", commits=1)
    advance_origin(origin_url, "web", commits=1)

    app = create_app()
    with TestClient(app) as client:
        client.post(
            "/setup",
            data={
                "username": "admin",
                "password": "senha-de-teste",
                "confirm_password": "senha-de-teste",
                "token": setup_state.token(),
            },
        )
        # Aponta o monitor para o repositório local (determinístico, sem rede).
        with session_scope() as session:
            runtime.save(
                session,
                {
                    "update_repo_url": origin_url,
                    "update_branch": "main",
                    "update_check_enabled": True,
                },
                rendered={"update_repo_url", "update_branch", "update_check_enabled"},
            )
        response = client.post("/settings/updates/check")
        check("POST check agenda e volta às configurações", response.status_code == 200)
        found = False
        for _ in range(40):
            page = client.get("/settings")
            if "commit(s) atrás" in page.text and "Novidade 1" in page.text:
                found = True
                break
            time.sleep(0.5)
        check("seção mostra os commits pendentes", found)
        home = client.get("/")
        check("aviso aparece no painel", "Há atualização" in home.text and "Detalhes" in home.text)


if __name__ == "__main__":
    raise SystemExit(main())
