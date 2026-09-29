"""Auditoria de segurança: as defesas precisam continuar valendo.

Cobre o que foi corrigido (segredo de sessão, token do assistente, catálogo só
com credencial, freio de força bruta, cabeçalhos, API atrás do login) e o que
precisa permanecer verdadeiro (hash de senha, destino interno no login).

Run with:  python tests/security.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="opds_sec_"))
os.chdir(WORKDIR)  # sem .env por perto: instalação nova de verdade
os.environ["DATA_DIR"] = str(WORKDIR)
os.environ["RSS_WORKER_ENABLED"] = "false"
os.environ["DB_ENCRYPTION"] = "auto"
os.environ.pop("SECRET_KEY", None)
os.environ.pop("ADMIN_PASSWORD", None)

PASSED: list[str] = []
FAILED: list[str] = []

SENHA = "senha-de-teste-123"
OPDS_SENHA = "segredo-do-opds-123"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def main() -> int:
    from fastapi.testclient import TestClient

    from app.config import DEFAULT_SECRET_KEY, get_base_settings
    from app.database.base import session_scope
    from app.main import create_app
    from app.security import auth, passwords, runtime, settings_store, throttle
    from app.security import setup as setup_state

    print("\n[segredo de sessão: o padrão público não vale]")
    base = get_base_settings()
    check("SECRET_KEY de fábrica não é usado", base.secret_key != DEFAULT_SECRET_KEY)
    check("segredo tem tamanho decente", len(base.secret_key) >= 32, str(len(base.secret_key)))
    arquivo = WORKDIR / "session.key"
    check("segredo persistido em DATA_DIR/session.key", arquivo.is_file())
    check("estável entre leituras", get_base_settings().secret_key == base.secret_key)
    if os.name != "nt":
        modo = arquivo.stat().st_mode & 0o777
        check("segredo com permissão 0600", modo == 0o600, oct(modo))

    print("\n[senhas: hash forte e comparação segura]")
    marca = passwords.hash_password(SENHA)
    check("hash não guarda a senha", SENHA not in marca and marca.startswith("pbkdf2_sha256$"))
    check("verifica senha correta", passwords.verify_password(SENHA, marca))
    check("recusa senha errada", not passwords.verify_password(SENHA + "x", marca))
    check("recusa hash corrompido", not passwords.verify_password(SENHA, "lixo"))
    check("recusa hash vazio", not passwords.verify_password(SENHA, None))

    print("\n[freio de força bruta (determinístico)]")
    throttle.clear()
    chave = throttle.key("admin", "10.0.0.9")
    for _ in range(throttle.MAX_FAILURES):
        throttle.register_failure(chave, now=1000.0)
    check("bloqueia depois de N falhas", throttle.blocked_for(chave, now=1001.0) > 0)
    check(
        "libera depois da janela de bloqueio",
        throttle.blocked_for(chave, now=1000.0 + throttle.LOCK_SECONDS + 1) == 0,
    )
    check("usuário diferente não é afetado", throttle.blocked_for(throttle.key("outro", "10.0.0.9"), now=1001.0) == 0)
    throttle.reset(chave)
    check("acerto zera o contador", throttle.blocked_for(chave, now=1001.0) == 0)
    throttle.clear()

    print("\n[destino do login é sempre interno]")
    check("aceita caminho interno", auth.safe_next("/library") == "/library")
    check("recusa protocolo-relativo", auth.safe_next("//evil.test") == "/")
    check("recusa host com barra invertida", auth.safe_next("/\\evil.test") == "/")
    check("recusa URL absoluta", auth.safe_next("http://evil.test") == "/")
    check("vazio vira a raiz", auth.safe_next(None) == "/")

    with TestClient(create_app()) as client:
        print("\n[assistente não pode ser assumido de fora]")
        check("GET /setup sem token responde 401", client.get("/setup", follow_redirects=False).status_code == 401)
        errado = client.get("/setup?token=chute", follow_redirects=False)
        check("token errado não mostra o formulário", errado.status_code == 401 and SENHA not in errado.text)
        certo = client.get(f"/setup?token={setup_state.token()}")
        check("token correto abre o assistente", certo.status_code == 200 and "Primeiro acesso" in certo.text)
        sem = client.post(
            "/setup",
            data={"username": "intruso", "password": "senha-do-intruso", "confirm_password": "senha-do-intruso"},
            follow_redirects=False,
        )
        check("POST /setup sem token é recusado", sem.status_code == 303 and "err=" in sem.headers.get("location", ""))
        with session_scope() as session:
            check("nada foi criado pelo intruso", settings_store.get(session, "admin_password_hash") is None)

        print("\n[catálogo fechado até existir credencial]")
        fechado = client.get("/opds", follow_redirects=False)
        check(
            "OPDS responde 401 (e não redireciona ao assistente)",
            fechado.status_code == 401 and "Basic" in fechado.headers.get("www-authenticate", ""),
            f"{fechado.status_code} {fechado.headers.get('www-authenticate', '')}",
        )
        v2 = client.get("/opds/v2", follow_redirects=False)
        check("OPDS 2.0 também exige credencial", v2.status_code == 401, str(v2.status_code))

        print("\n[configuração do assistente (com credencial do OPDS)]")
        pronto = client.post(
            "/setup",
            data={
                "token": setup_state.token(),
                "username": "admin",
                "password": SENHA,
                "confirm_password": SENHA,
                "app_name": "Biblioteca Segura",
                "opds_username": "leitor",
                "opds_password": OPDS_SENHA,
                "require_auth_panel": "on",
            },
            follow_redirects=False,
        )
        check("assistente conclui", pronto.status_code == 303 and pronto.headers["location"].startswith("/?"), pronto.headers.get("location", ""))
        check("painel abre logado", client.get("/").status_code == 200)
        print("\n[o assistente não desliga o que nem aparece nele]")
        check(
            "login do painel continua exigido",
            runtime.get("require_auth_panel") is not False,
            str(runtime.get("require_auth_panel")),
        )
        check(
            "links seguem o host do cliente",
            runtime.get("use_request_host") is not False,
            str(runtime.get("use_request_host")),
        )
        with session_scope() as session:
            check("credencial marcada como confirmada", auth.credentials_confirmed(session))
            guardado = settings_store.get(session, "opds_password_hash") or ""
            check("senha do OPDS guardada como hash", OPDS_SENHA not in guardado and bool(guardado))

        print("\n[catálogo: credencial correta entra, errada não]")
        anonimo = client.get("/opds", follow_redirects=False)
        check("sem credencial: 401", anonimo.status_code == 401)
        check("pede autenticação por Basic", "Basic" in anonimo.headers.get("www-authenticate", ""))
        check("senha errada: 401", client.get("/opds", auth=("leitor", "errada")).status_code == 401)
        check("usuário errado: 401", client.get("/opds", auth=("outro", OPDS_SENHA)).status_code == 401)
        bom = client.get("/opds", auth=("leitor", OPDS_SENHA))
        check(
            "credencial correta entrega o Atom",
            bom.status_code == 200 and "atom+xml" in bom.headers.get("content-type", ""),
            f"{bom.status_code} {bom.headers.get('content-type', '')}",
        )
        check("inclusive o download e a capa", client.get("/opds/all", auth=("leitor", OPDS_SENHA)).status_code == 200)

        print("\n[a senha do OPDS não vaza para a tela]")
        config = client.get("/settings").text
        check("Configurações não mostra a senha do OPDS", OPDS_SENHA not in config)
        check("nem o hash", not (settings_store.get.__name__ and "pbkdf2" in config))

        print("\n[cabeçalhos de segurança]")
        pagina = client.get("/")
        for nome, esperado in (
            ("x-content-type-options", "nosniff"),
            # SAMEORIGIN: o Web Reader enquadra o próprio conteúdo (capítulo de
            # EPUB, PDF). Terceiros continuam barrados.
            ("x-frame-options", "SAMEORIGIN"),
            ("referrer-policy", "no-referrer"),
        ):
            valor = pagina.headers.get(nome, "")
            check(f"{nome}: {esperado}", valor == esperado, valor)
        csp = pagina.headers.get("content-security-policy", "")
        check("CSP sem origem externa", "default-src 'self'" in csp, csp[:60])
        check("CSP permite enquadrar a própria origem", "frame-ancestors 'self'" in csp, csp)
        check("CSP bloqueia enquadramento por terceiros", "frame-ancestors 'none'" not in csp)
        check("HSTS ausente em HTTP", pagina.headers.get("strict-transport-security") is None)
        check("cabeçalhos também nas respostas do OPDS", client.get("/opds", auth=("leitor", OPDS_SENHA)).headers.get("x-content-type-options") == "nosniff")

        print("\n[API e especificação atrás do login]")
        check("especificação com sessão", client.get("/api/openapi.json").status_code == 200)
        check("interface do Swagger não existe (sem CDN)", client.get("/api/docs").status_code == 404)

        print("\n[sem sessão, nada de painel nem de API]")
        client.get("/logout")
        check("painel volta ao login", client.get("/", follow_redirects=False).status_code == 303)
        for rota in ("/api/system/settings", "/api/books", "/api/conversions/targets", "/api/openapi.json"):
            resposta = client.get(rota, follow_redirects=False)
            check(f"{rota} exige login", resposta.status_code in (303, 401), str(resposta.status_code))

        print("\n[login: freio ligado de ponta a ponta]")
        throttle.clear()
        codigos = []
        for _ in range(throttle.MAX_FAILURES + 1):
            codigos.append(client.post("/login", data={"username": "admin", "password": "errada", "next": "/"}).status_code)
        check("falhas viram bloqueio (429)", codigos[-1] == 429, str(codigos))
        check("aviso explica o bloqueio", "Muitas tentativas" in client.post("/login", data={"username": "admin", "password": "errada"}).text)
        throttle.clear()
        bom_login = client.post(
            "/login",
            data={"username": "admin", "password": SENHA, "next": "/"},
            follow_redirects=False,
        )
        check("senha certa volta a entrar", bom_login.status_code == 303, str(bom_login.status_code))
        check("e o contador zera", throttle.blocked_for(throttle.key("admin", "testclient")) == 0)

        print("\n[login não redireciona para fora]")
        client.get("/logout")
        tentativa = client.post(
            "/login",
            data={"username": "admin", "password": SENHA, "next": "//evil.test"},
            follow_redirects=False,
        )
        check("destino externo é ignorado", tentativa.headers.get("location") == "/", tentativa.headers.get("location", ""))

        print("\n[modo aberto continua possível (escolha explícita)]")
        with session_scope() as session:
            runtime.save(session, {"opds_require_auth": False, "opds_username": ""})
        check("catálogo aberto quando desligado", client.get("/opds").status_code == 200)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    import shutil

    shutil.rmtree(WORKDIR, ignore_errors=True)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
