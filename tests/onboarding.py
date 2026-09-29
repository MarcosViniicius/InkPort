"""Assistente de primeiro acesso e configuração guardada no banco.

Cobre o caminho de quem NÃO tem ``.env``: o painel abre em ``/setup``, o usuário
cria a senha e ajusta o essencial, tudo vai para o SQLite e passa a valer na
hora. Roda num diretório temporário (sem ``.env``) de propósito.

Run with:  python tests/onboarding.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="opds_setup_"))
os.chdir(WORKDIR)  # sem .env por perto: é o cenário de instalação nova
os.environ["DATA_DIR"] = str(WORKDIR)
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["RSS_WORKER_ENABLED"] = "false"
os.environ["DB_ENCRYPTION"] = "auto"
os.environ.pop("ADMIN_PASSWORD", None)
os.environ.pop("ADMIN_USERNAME", None)

PASSED: list[str] = []
FAILED: list[str] = []

SENHA = "senha-boa-123"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def main() -> int:
    from fastapi.testclient import TestClient

    from app.config import get_base_settings
    from app.database.base import session_scope
    from app.main import create_app
    from app.security import auth, runtime, settings_store
    from app.security import setup as setup_state

    print("\n[estado inicial: sem senha configurada]")
    check(
        "nenhuma senha veio da configuração",
        "admin_password" not in get_base_settings().model_fields_set,
    )
    check("valor padrão continua valendo", runtime.get("app_name") == get_base_settings().app_name)

    with TestClient(create_app()) as client:
        print("\n[o painel todo manda para o assistente]")
        for rota in ("/", "/library", "/import", "/conversions", "/devices", "/feeds", "/settings"):
            resposta = client.get(rota, follow_redirects=False)
            destino = resposta.headers.get("location", "")
            check(f"{rota} -> /setup", resposta.status_code == 303 and destino == "/setup", f"{resposta.status_code} {destino}")
        resposta = client.get("/api/system/settings")
        check("API responde 503 enquanto não configurado", resposta.status_code == 503, str(resposta.status_code))
        resposta = client.get("/opds")
        check(
            "OPDS não é sequestrado pelo assistente (responde por si)",
            resposta.status_code in (200, 401) and "/setup" not in resposta.headers.get("location", ""),
            str(resposta.status_code),
        )
        check("health segue acessível", client.get("/health").status_code == 200)

        print("\n[a página do assistente]")
        resposta = client.get(f"/setup?token={setup_state.token()}")
        check("abre com o formulário", resposta.status_code == 200 and "Primeiro acesso" in resposta.text)
        check("traz os campos do catálogo", "Raiz do OPDS" in resposta.text and "Nome da aplicação" in resposta.text)
        check("pede usuário e senha", 'name="password"' in resposta.text and 'name="confirm_password"' in resposta.text)

        print("\n[sem token não dá para assumir a instalação]")
        sem_token = client.post(
            "/setup",
            data={"username": "intruso", "password": "senha-do-intruso", "confirm_password": "senha-do-intruso"},
            follow_redirects=False,
        )
        check(
            "POST /setup sem token é recusado",
            sem_token.status_code == 303 and "err=" in sem_token.headers["location"],
            sem_token.headers.get("location", ""),
        )
        token_errado = client.get("/setup?token=chute", follow_redirects=False)
        check(
            "GET /setup com token errado não mostra o formulário",
            token_errado.status_code == 401 and "Token de primeiro acesso" in token_errado.text,
            str(token_errado.status_code),
        )

        print("\n[validações]")
        curta = client.post(
            "/setup",
            data={"username": "admin", "password": "123", "confirm_password": "123", "token": setup_state.token()},
            follow_redirects=False,
        )
        check("senha curta é recusada", curta.status_code == 303 and "err=" in curta.headers["location"])
        diferente = client.post(
            "/setup",
            data={"username": "admin", "password": SENHA, "confirm_password": "outra-coisa"},
            follow_redirects=False,
        )
        check("senhas diferentes são recusadas", diferente.status_code == 303 and "err=" in diferente.headers["location"])
        resposta = client.get("/", follow_redirects=False)
        check(
            "erro de validação não cria nada (painel segue no assistente)",
            resposta.status_code == 303 and resposta.headers.get("location") == "/setup",
            f"{resposta.status_code} {resposta.headers.get('location', '')}",
        )
        with session_scope() as session:
            check(
                "nenhuma credencial foi gravada",
                settings_store.get(session, "admin_password_hash") is None,
            )

        print("\n[configuração concluída]")
        dados = {
            "username": "marco",
            "password": SENHA,
            "confirm_password": SENHA,
            "app_name": "Biblioteca do Marco",
            "opds_root_mode": "navigation",
            "opds_username": "leitor",
            "opds_password": "senha-opds-forte",
            "base_url": "http://exemplo.test:8080",
            "use_request_host": "on",
            "conversion_concurrency": "3",
            "conversion_timeout": "600",
            "max_upload_mb": "512",
            "storage_limit_gb": "12.5",
            "rss_worker_enabled": "on",
            "require_auth_panel": "on",
            "token": setup_state.token(),
        }
        pronto = client.post("/setup", data=dados, follow_redirects=False)
        check("assistente conclui e volta ao painel", pronto.status_code == 303 and pronto.headers["location"].startswith("/?"), pronto.headers.get("location", ""))
        painel = client.get("/")
        check("já entra logado e o painel renderiza", painel.status_code == 200 and "Painel" in painel.text, str(painel.status_code))
        check("o nome escolhido aparece no painel", "Biblioteca do Marco" in painel.text)

        print("\n[o assistente fecha]")
        resposta = client.get("/setup", follow_redirects=False)
        check("GET /setup redireciona", resposta.status_code == 303 and resposta.headers["location"] == "/")
        resposta = client.post("/setup", data=dados, follow_redirects=False)
        check("POST /setup redireciona", resposta.status_code == 303 and resposta.headers["location"] == "/")

        print("\n[o que foi configurado vale e persiste]")
        with session_scope() as session:
            runtime.reset()
            runtime.load(session)
            check("nome no banco", runtime.get("app_name") == "Biblioteca do Marco", str(runtime.get("app_name")))
            check("raiz do OPDS no banco", runtime.get("opds_root_mode") == "navigation")
            check("inteiro validado", runtime.get("conversion_concurrency") == 3, str(runtime.get("conversion_concurrency")))
            check("número decimal validado", runtime.get("storage_limit_gb") == 12.5, str(runtime.get("storage_limit_gb")))
            check("booleano ligado", runtime.get("rss_worker_enabled") is True)
            guardado = settings_store.get(session, "opds_password_hash") or ""
            check("senha do OPDS guardada como hash", bool(guardado) and "senha-opds-forte" not in guardado)
            credencial = settings_store.get(session, "admin_password_hash") or ""
            check("senha do painel guardada como hash", bool(credencial) and SENHA not in credencial)

        print("\n[OPDS com Basic auth por hash]")
        anonimo = client.get("/opds", follow_redirects=False)
        check("sem credenciais pede autenticação", anonimo.status_code == 401, str(anonimo.status_code))
        errado = client.get("/opds", auth=("leitor", "senha-errada"))
        check("senha errada é recusada", errado.status_code == 401, str(errado.status_code))
        certo = client.get("/opds", auth=("leitor", "senha-opds-forte"))
        check("senha certa abre o catálogo", certo.status_code == 200, str(certo.status_code))

        print("\n[a tela de Configurações usa o mesmo registro]")
        pagina = client.get("/settings")
        check("card de configuração presente", "Aplicação e catálogo" in pagina.text)
        check("mostra o valor atual", 'value="Biblioteca do Marco"' in pagina.text)
        check("campo secreto não devolve a senha", "senha-opds-forte" not in pagina.text)

        salvo = client.post(
            "/settings/app",
            data={**dados, "app_name": "Outro Nome", "storage_limit_gb": "0", "rss_worker_enabled": ""},
            follow_redirects=False,
        )
        check("salvar em Configurações funciona", salvo.status_code == 303 and "ok=" in salvo.headers["location"], salvo.headers.get("location", ""))
        with session_scope() as session:
            runtime.reset()
            runtime.load(session)
            check("novo nome aplicado", runtime.get("app_name") == "Outro Nome")
            check("caixa desmarcada desliga o RSS", runtime.get("rss_worker_enabled") is False)
            check("teto zero aceito", runtime.get("storage_limit_gb") == 0)

        excessivo = client.post(
            "/settings/app",
            data={**dados, "conversion_concurrency": "999"},
            follow_redirects=False,
        )
        with session_scope() as session:
            runtime.reset()
            runtime.load(session)
            check("valor acima do máximo é limitado", runtime.get("conversion_concurrency") == 8, str(runtime.get("conversion_concurrency")))
        check("limite aplicado sem erro para o usuário", excessivo.status_code == 303)

        print("\n[migração: senha antiga (vinda do .env) passa pelo assistente uma vez]")
        with session_scope() as session:
            # É o caso de quem já tinha senha no .env e nunca a definiu no painel.
            settings_store.set_value(session, "admin_confirmed", None)
            session.commit()
            setup_state.refresh(session)
        resposta = client.get("/", follow_redirects=False)
        check(
            "senha não confirmada volta ao assistente",
            resposta.status_code == 303 and resposta.headers.get("location") == "/setup",
            f"{resposta.status_code} {resposta.headers.get('location', '')}",
        )
        pagina = client.get(f"/setup?token={setup_state.token()}")
        check("assistente reaparece para assumir a credencial", pagina.status_code == 200)
        check("usuário atual já vem preenchido", 'value="marco"' in pagina.text)
        denovo = client.post(
            "/setup",
            data={"username": "marco", "password": "senha-nova-123",
              "confirm_password": "senha-nova-123", "token": setup_state.token()},
            follow_redirects=False,
        )
        check(
            "concluir de novo libera o painel",
            denovo.status_code == 303 and denovo.headers["location"].startswith("/?"),
            denovo.headers.get("location", ""),
        )
        check("painel volta a abrir", client.get("/").status_code == 200)
        with session_scope() as session:
            check("credencial marcada como confirmada", auth.credentials_confirmed(session) is True)
            check(
                "e o assistente não volta mais",
                setup_state.refresh(session) is True,
            )

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    import shutil

    shutil.rmtree(WORKDIR, ignore_errors=True)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
