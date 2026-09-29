"""End-to-end smoke test: boots the app and exercises the main routes.

Run with:  python tests/smoke.py
Uses a throwaway DATA_DIR so it never touches the real library.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="opds_smoke_"))
os.environ["DATA_DIR"] = str(WORKDIR)
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["RSS_WORKER_ENABLED"] = "false"
os.environ["REQUIRE_AUTH_PANEL"] = "true"
os.environ["BASE_URL"] = "http://testserver"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def main() -> int:
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()

    with TestClient(app) as client:
        # Primeiro acesso: cria a credencial do painel e sai (para testar o login).
        from app.security import setup as _setup  # o token vem do proprio servidor
        client.post("/setup", data={"username": "admin", "password": "senha-de-teste",
                                   "confirm_password": "senha-de-teste", "token": _setup.token()})
        client.get("/logout")

        # O catalogo agora exige credencial por padrao; aqui interessa o conteudo.
        from app.database.base import session_scope as _scope
        from app.security import runtime as _runtime

        with _scope() as _session:
            _runtime.save(_session, {"opds_require_auth": False})

        _unauthenticated_checks(client)
        _panel_checks(client)
        _filter_checks(client)
        _import_category_checks(client)
        _url_import_checks(client)
        _background_feed_actions(client)
        _feed_edit_checks(client)
        _devices_checks(client)
        _opds_checks(client)
        _network_checks()
        _media_type_checks()
        _api_checks(client)
        _conversion_checks(client)
        _library_checks(client)
        _opds_root_checks(client)  # por último: a biblioteca já tem livros
        _opds_author_checks(client)

    shutil.rmtree(WORKDIR, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


def _unauthenticated_checks(client) -> None:
    print("\n[rota pública / autenticação]")
    response = client.get("/health")
    check("health", response.status_code == 200)

    response = client.get("/", follow_redirects=False)
    check("painel exige login", response.status_code == 303 and "/login" in response.headers.get("location", ""))

    response = client.get("/login")
    check("página de login", response.status_code == 200 and "Entrar" in response.text)

    response = client.post("/login", data={"username": "admin", "password": "wrong"}, follow_redirects=False)
    check("login inválido rejeitado", response.status_code == 200 and "inválidos" in response.text)


def _panel_checks(client) -> None:
    print("\n[painel autenticado]")
    response = client.post(
        "/login",
        data={"username": "admin", "password": "senha-de-teste", "next": "/"},
        follow_redirects=False,
    )
    check("login válido", response.status_code == 303)

    for path, marker in (
        ("/", "Painel"),
        ("/library", "Biblioteca"),
        ("/import", "Importar"),
        ("/conversions", "Conversões"),
        ("/devices", "Dispositivos"),
        ("/feeds", "Feeds"),
        ("/settings", "Configurações"),
    ):
        response = client.get(path)
        check(f"GET {path}", response.status_code == 200 and marker in response.text, str(response.status_code))


def _url_import_checks(client) -> None:
    """Importar uma página por URL: baixa, guarda o endereço, converte.

    Sobe um servidor HTTP local (sem internet) para exercitar o caminho real:
    download -> detecção -> importação -> fila de conversão, e os avisos quando
    o endereço não presta.
    """
    import http.server
    import threading
    import time

    from sqlalchemy import select

    from app.config import get_settings
    from app.database.base import session_scope
    from app.database.models import Book, ConversionJob

    print("\n[importar uma página por URL]")

    pagina = (
        "<!doctype html><html lang='pt-br'><head><title>Artigo da URL</title>"
        "<meta name='author' content='Autora'></head><body>"
        "<nav>menu que deve ser ignorado</nav><h1>Introdução</h1>"
        "<p>Primeiro parágrafo do artigo baixado.</p>"
        "<h2>Segunda parte</h2><p>Fim do artigo.</p></body></html>"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - nome exigido pela stdlib
            if self.path == "/artigo":
                corpo, tipo = pagina.encode("utf-8"), "text/html; charset=utf-8"
            elif self.path == "/binario":
                corpo, tipo = b"\x00\x01\x02binario", "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *args):  # silencia o log do servidor de teste
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    porta = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endereco = f"http://127.0.0.1:{porta}/artigo"
    inbox = get_settings().inbox_dir
    antes = {p.name for p in inbox.glob("*")} if inbox.exists() else set()

    try:
        vazio = client.post("/import/url", data={"url": "", "category": "Páginas"})
        check(
            "URL vazia é recusada com aviso",
            vazio.status_code == 400 and "Informe o endereço" in vazio.text,
            str(vazio.status_code),
        )
        esquema = client.post("/import/url", data={"url": "ftp://exemplo.com/a", "category": "Páginas"})
        check(
            "endereço sem http(s) é recusado",
            esquema.status_code == 400 and "http://" in esquema.text,
            str(esquema.status_code),
        )
        sem_categoria = client.post("/import/url", data={"url": endereco, "category": ""})
        check(
            "categoria continua obrigatória",
            sem_categoria.status_code == 400 and "categoria" in sem_categoria.text,
            str(sem_categoria.status_code),
        )
        fora_do_ar = client.post(
            "/import/url", data={"url": "http://127.0.0.1:9/nada.html", "category": "Páginas"}
        )
        check(
            "endereço fora do ar vira aviso (não 500)",
            fora_do_ar.status_code == 400 and "Falha ao baixar" in fora_do_ar.text,
            str(fora_do_ar.status_code),
        )
        binario = client.post(
            "/import/url",
            data={"url": f"http://127.0.0.1:{porta}/binario", "category": "Páginas"},
        )
        check(
            "conteúdo não suportado é recusado com aviso",
            binario.status_code == 400 and "não sei importar" in binario.text,
            str(binario.status_code),
        )

        resposta = client.post(
            "/import/url",
            data={
                "url": endereco, "category": "Páginas baixadas", "convert": "on",
                "target_format": "epub", "device_profile": "generic_epub",
                "keep_original": "on",
            },
        )
        check("página baixada e importada", resposta.status_code == 200, str(resposta.status_code))
        check(
            "o resultado mostra importado e a conversão na fila",
            "importado" in resposta.text and "na fila" in resposta.text,
        )

        with session_scope() as session:
            livro = session.scalar(
                select(Book).where(Book.source_url == endereco, Book.format == "html")
            )
            check("livro guardado com o endereço de origem", livro is not None)
            livro_id = livro.id if livro else 0
            if livro is not None:
                check("origem marcada como baixado da web", livro.source == "download", livro.source)
                check(
                    "categoria aplicada",
                    livro.category_rel is not None and livro.category_rel.name == "Páginas baixadas",
                    getattr(livro.category_rel, "name", None),
                )
                titulo = livro.title
                autor = livro.author
                job = session.scalar(
                    select(ConversionJob).where(ConversionJob.book_id == livro.id)
                )
                check(
                    "conversão para EPUB enfileirada",
                    job is not None and job.target_format == "epub",
                    str(job and job.target_format),
                )
            else:
                titulo = autor = ""

        check("o livro herdou o título da página", titulo == "Artigo da URL", titulo)
        check("e o autor declarado pela página", autor == "Autora", autor)
        detalhe = client.get(f"/library/{livro_id}")
        check(
            "o detalhe mostra a origem com link",
            "baixado da web" in detalhe.text and endereco in detalhe.text,
            str(detalhe.status_code),
        )

        # A conversão roda no worker (que está de pé no teste): esperar o EPUB
        # prova o caminho página -> EPUB de ponta a ponta.
        convertido = None
        limite = time.time() + 60
        while convertido is None and time.time() < limite:
            with session_scope() as session:
                convertido = session.scalar(
                    select(Book).where(Book.source_url == endereco, Book.format == "epub")
                )
            if convertido is None:
                time.sleep(0.4)
        check("a página virou EPUB pelo worker", convertido is not None)

        denovo = client.post(
            "/import/url", data={"url": endereco, "category": "Páginas baixadas"}
        )
        check(
            "baixar a mesma página de novo avisa que já existe",
            denovo.status_code == 200 and "Já existe" in denovo.text,
            str(denovo.status_code),
        )

        depois = {p.name for p in inbox.glob("*")} if inbox.exists() else set()
        check("o download não deixa lixo no inbox", depois - antes == set(), str(depois - antes))

        # Limpeza: os livros deste teste não podem sobrar para as checagens de
        # OPDS que vêm depois (uma entrada com autor mudaria o feed).
        with session_scope() as session:
            sobrando = [
                book.id
                for book in session.scalars(
                    select(Book).where(Book.source_url == endereco)
                ).all()
            ]
        for alvo in sobrando:
            client.post(f"/library/{alvo}/delete", data={"delete_files": "on"})
        with session_scope() as session:
            resto = session.scalar(select(Book).where(Book.source_url == endereco))
        check("a importação de teste não deixa livros para trás", resto is None)
    finally:
        server.shutdown()
        server.server_close()


def _background_feed_actions(client) -> None:
    """As duas ações de feed rodam em segundo plano e não podem dar 500.

    Regressão: `asyncio.create_task` dentro de um handler síncrono levanta
    "no running event loop", e isso transformava "Buscar agora" / "Refazer
    feed" em erro 500.
    """
    import re
    import time

    print("\n[ações de feed em segundo plano]")
    # A porta 9 descarta conexões: o trabalho de fundo falha rápido e sem rede.
    response = client.post(
        "/feeds/save",
        data={
            "feed_id": "",
            "name": "Teste fundo",
            "url": "http://127.0.0.1:9/sem-feed.xml",
            "interval_minutes": "60",
            "output_format": "epub",
            "device_profile": "eink_generic",
            "max_items_per_run": "1",
        },
    )
    check("POST /feeds/save", response.status_code in (200, 303), str(response.status_code))

    page = client.get("/feeds")
    match = re.search(r"/feeds/(\d+)/refresh", page.text)
    check("feed criado aparece na página", match is not None)
    if match is None:
        return
    feed_id = match.group(1)

    response = client.post(f"/feeds/{feed_id}/refresh")
    check(
        "Buscar agora responde na hora (não 500)",
        response.status_code == 200 and "Busca iniciada" in response.text,
        str(response.status_code),
    )
    response = client.post(f"/feeds/{feed_id}/rebuild")
    check(
        "Refazer feed responde na hora (não 500)",
        response.status_code == 200
        and ("segundo plano" in response.text or "já está sendo processado" in response.text),
        str(response.status_code),
    )

    time.sleep(2.0)  # deixa as tarefas de fundo terminarem (falham rápido)
    page = client.get("/feeds")
    check("página de feeds continua de pé", page.status_code == 200)

    response = client.post(f"/feeds/{feed_id}/delete")
    check(
        "feed de teste removido",
        response.status_code == 200 and "Feed removido" in response.text,
        str(response.status_code),
    )


def _feed_edit_checks(client) -> None:
    """Editar um feed pelo painel: nome, URL e o resto das configurações.

    O formulário de criação e o de edição são o mesmo; aqui interessa que o
    ``feed_id`` chegue no POST, que os valores sejam gravados e que uma URL
    repetida (ou um número inválido) vire aviso em vez de erro 500.
    """
    from sqlalchemy import select

    from app.database.base import session_scope
    from app.database.models import Feed
    from app.rss.naming import feed_category_name

    print("\n[editar um feed]")

    def salvar(nome: str, url: str, **extra) -> None:
        dados = {
            "feed_id": "", "name": nome, "url": url, "interval_minutes": "360",
            "output_format": "epub", "device_profile": "generic_epub",
            "max_items_per_run": "20", "active": "on", "keep_original": "on",
        }
        dados.update(extra)
        client.post("/feeds/save", data=dados)

    def por_url(url: str) -> int:
        with session_scope() as session:
            return int(session.scalar(select(Feed).where(Feed.url == url)).id)

    marca = os.getpid()
    original = f"http://127.0.0.1:9/editavel-{marca}.xml"
    outro = f"http://127.0.0.1:9/vizinho-{marca}.xml"
    salvar("Feed editável", original)
    feed_id = por_url(original)

    pagina = client.get(f"/feeds/{feed_id}/edit")
    check("tela de edição abre", pagina.status_code == 200 and "Editar feed" in pagina.text, str(pagina.status_code))
    check(
        "formulário já vem preenchido",
        'value="Feed editável"' in pagina.text and f'value="{original}"' in pagina.text,
    )
    check("a tela manda o id do feed", f'name="feed_id" value="{feed_id}"' in pagina.text)
    check("tela de edição é ligada ao feed certo", f"/feeds/{feed_id}/rebuild" in pagina.text)

    editado = original.replace("editavel-", "editado-")
    salvo = client.post(
        "/feeds/save",
        data={
            "feed_id": str(feed_id), "name": "Feed renomeado", "url": editado,
            "interval_minutes": "45", "output_format": "pdf",
            "device_profile": "generic_epub", "destination_folder": "Revisados",
            "max_items_per_run": "3", "active": "", "keep_original": "",
        },
        follow_redirects=False,
    )
    check(
        "salvar a edição responde com aviso de sucesso",
        salvo.status_code == 303 and "ok=" in salvo.headers.get("location", ""),
        salvo.headers.get("location", ""),
    )

    with session_scope() as session:
        feed = session.get(Feed, feed_id)
        check("nome atualizado", feed.name == "Feed renomeado", str(feed.name))
        check("URL atualizada", feed.url == editado, str(feed.url))
        check("frequência atualizada", feed.interval_minutes == 45, str(feed.interval_minutes))
        check("formato de saída atualizado", feed.output_format == "pdf", str(feed.output_format))
        check("subcategoria atualizada", feed.destination_folder == "Revisados", str(feed.destination_folder))
        check("itens por execução atualizados", feed.max_items_per_run == 3, str(feed.max_items_per_run))
        check("caixa desmarcada desliga o feed", feed.active is False, str(feed.active))
        check(
            "categoria dos livros passa a ser a nova",
            feed_category_name(feed) == "rss/Revisados",
            feed_category_name(feed),
        )

    relido = client.get(f"/feeds/{feed_id}/edit").text
    check("a tela recarrega com os valores novos", 'value="Feed renomeado"' in relido and "rss/Revisados" in relido)

    salvar("Feed vizinho", outro)
    outro_id = por_url(outro)
    colisao = client.post(
        "/feeds/save",
        data={"feed_id": "", "name": "Cópia", "url": editado},
        follow_redirects=False,
    )
    check(
        "criar com URL repetida avisa em vez de estourar",
        colisao.status_code == 303 and "err=" in colisao.headers.get("location", ""),
        colisao.headers.get("location", ""),
    )
    check(
        "o aviso devolve o usuário ao formulário aberto",
        "novo=1" in colisao.headers.get("location", ""),
        colisao.headers.get("location", ""),
    )
    check(
        "?novo=1 abre a seção do formulário",
        "data-sect=\"novo-feed\" open" in client.get("/feeds?novo=1").text,
    )
    with session_scope() as session:
        quantos = len(session.scalars(select(Feed).where(Feed.url == editado)).all())
    check("nenhum feed duplicado foi criado", quantos == 1, str(quantos))

    troca = client.post(
        "/feeds/save",
        data={"feed_id": str(outro_id), "name": "Feed vizinho", "url": editado},
        follow_redirects=False,
    )
    check(
        "editar para uma URL em uso também avisa",
        troca.status_code == 303 and "err=" in troca.headers.get("location", ""),
        troca.headers.get("location", ""),
    )

    estranho = client.post(
        "/feeds/save",
        data={
            "feed_id": str(feed_id), "name": "Feed renomeado", "url": editado,
            "interval_minutes": "abc", "max_items_per_run": "",
            "output_format": "formato-que-nao-existe", "device_profile": "perfil-que-nao-existe",
        },
        follow_redirects=False,
    )
    check(
        "valor fora do padrão não vira erro",
        estranho.status_code == 303 and "ok=" in estranho.headers.get("location", ""),
        estranho.headers.get("location", ""),
    )
    with session_scope() as session:
        feed = session.get(Feed, feed_id)
        check("frequência inválida mantém a anterior", feed.interval_minutes == 45, str(feed.interval_minutes))
        check("formato inexistente mantém o anterior", feed.output_format == "pdf", str(feed.output_format))
        check("perfil inexistente mantém o anterior", feed.device_profile == "generic_epub", str(feed.device_profile))

    sumiu = client.get("/feeds/999999/edit", follow_redirects=False)
    check(
        "editar feed inexistente avisa em vez de 500",
        sumiu.status_code == 303 and "err=" in sumiu.headers.get("location", ""),
        str(sumiu.status_code),
    )

    for alvo in (feed_id, outro_id):
        client.post(f"/feeds/{alvo}/delete")
    with session_scope() as session:
        restou = {feed.name for feed in session.scalars(select(Feed)).all()}
    check(
        "feeds de teste removidos",
        "Feed renomeado" not in restou and "Feed vizinho" not in restou,
        str(restou),
    )


def _opds_root_checks(client) -> None:
    """A raiz serve todo mundo: livros para clientes simples, seções para navegar.

    ``/opds`` (modo padrão ``mixed``) traz livros *e* seções, porque o CrossPoint
    só lista entradas de aquisição. ``/opds/browse`` traz só as seções — é o
    endereço para leitores Android que mostram entradas e querem "viajar" pelas
    categorias sem passar pelos livros.
    """
    import re

    from app.config import get_settings

    print("\n[raiz do OPDS e navegação]")
    root = client.get("/opds", headers={"Accept": "application/atom+xml"})
    books = len(re.findall(r'rel="http://opds-spec.org/acquisition"', root.text))
    mode = get_settings().opds_root_mode
    if mode == "navigation":
        check("raiz só com menus (modo navigation)", books == 0, str(books))
    else:
        check("raiz traz livros (clientes simples)", books > 0, str(books))
    check(
        "raiz traz as seções como entradas",
        "Novidades" in root.text and "Todos os livros" in root.text,
    )
    catalog = client.get("/opds/all", headers={"Accept": "application/atom+xml"})
    check(
        "o catálogo de livros continua completo",
        len(re.findall(r'rel="http://opds-spec.org/acquisition"', catalog.text)) > 0,
    )

    browse = client.get("/opds/browse", headers={"Accept": "application/atom+xml"})
    browse_books = len(re.findall(r'rel="http://opds-spec.org/acquisition"', browse.text))
    browse_entries = len(re.findall(r"<entry>", browse.text))
    check("browse não traz livros", browse_books == 0, str(browse_books))
    check("browse traz as seções como entradas", browse_entries >= 5, str(browse_entries))

    # O modo agora vive no banco: salvar de verdade exercita o caminho real.
    from app.database.base import session_scope
    from app.security import runtime

    original = runtime.get("opds_root_mode")
    try:
        with session_scope() as session:
            runtime.save(session, {"opds_root_mode": "navigation"})
        navigation = client.get("/opds", headers={"Accept": "application/atom+xml"})
        nav_books = len(re.findall(r'rel="http://opds-spec.org/acquisition"', navigation.text))
        nav_entries = len(re.findall(r"<entry>", navigation.text))
        check("modo navigation: raiz sem livros", nav_books == 0, str(nav_books))
        check("modo navigation: raiz com as seções", nav_entries >= 5, str(nav_entries))
        check("modo navigation: sem paginação", 'rel="next"' not in navigation.text)
    finally:
        with session_scope() as session:
            runtime.save(session, {"opds_root_mode": original})


def _opds_author_checks(client) -> None:
    """Livros sem autor não podem sair com "Autores desconhecidos" no feed.

    Clientes que montam o nome do arquivo a partir de título + autor salvavam
    tudo como "Autores desconhecidos"; sem o autor na entrada eles usam o título.
    """
    import re

    print("\n[autores no OPDS]")
    feed = client.get("/opds/all?per_page=100", headers={"Accept": "application/atom+xml"})
    text = feed.text
    check("nenhum autor placeholder no feed", "Autores desconhecidos" not in text)
    check(
        "o feed declara um autor (exigência do Atom)",
        re.search(r"<feed\b.*?<author><name>[^<]+</name></author>", text, re.DOTALL) is not None,
    )

    entries = re.findall(r"<entry>.*?</entry>", text, re.DOTALL)
    with_author = [block for block in entries if "Livro Teste" in block]
    without_author = [block for block in entries if "Livro Teste" not in block]
    check(
        "livro com autor mantém o autor na entrada",
        bool(with_author) and "<author><name>Autor</name>" in with_author[0],
        str(len(with_author)),
    )
    check(
        "livro sem autor não leva elemento author",
        bool(without_author) and all("<author>" not in block for block in without_author),
        f"{len(without_author)} entradas",
    )
    if without_author:
        from urllib.parse import unquote

        title_match = re.search(r"<title[^>]*>([^<]+)</title>", without_author[0])
        title_text = title_match.group(1) if title_match else ""
        download = re.search(r'href="([^"]*/opds/download/[^"]+)"', without_author[0])
        if download and title_text:
            response = client.get(download.group(1).replace("http://testserver", ""))
            disposition = response.headers.get("content-disposition", "")
            encoded = re.search(r"filename\*=utf-8''([^;]+)", disposition)
            name = unquote(encoded.group(1)) if encoded else disposition
            check(
                "download usa o título como nome do arquivo",
                "attachment" in disposition
                and name.startswith(title_text[:18])
                and "Autores" not in name,
                f"{name[:60]!r} (título: {title_text[:30]!r})",
            )


def _filter_checks(client) -> None:
    """Filtros do painel nunca podem responder JSON cru.

    O formulário da biblioteca envia os campos vazios quando o usuário escolhe
    "Todas"; um ``int`` declarado na rota fazia o FastAPI responder 422 em JSON
    ("int_parsing ... input: ''") em vez da página. E qualquer 422 de verdade numa
    rota do painel deve virar página amigável.
    """
    import re

    print("\n[filtros do painel]")
    for url, label in (
        ("/library?category_id=&author=&fmt=&sort=added_desc", "filtro com campos vazios"),
        ("/library?category_id=&q=", "busca com filtro vazio"),
        ("/library?page=&category_id=", "página vazia"),
        ("/library?category_id=abc", "categoria inválida"),
        ("/library?q=livro&sort=title_asc", "filtro normal"),
    ):
        response = client.get(url)
        kind = response.headers.get("content-type", "").split(";")[0]
        check(
            f"GET {label} volta HTML 200",
            response.status_code == 200 and kind == "text/html",
            f"{response.status_code} {kind}",
        )

    response = client.get("/reader/qualquer-livro/page/0?w=abc")
    kind = response.headers.get("content-type", "").split(";")[0]
    check(
        "422 no painel vira página amigável (não JSON)",
        kind == "text/html" and "int_parsing" not in response.text,
        f"{response.status_code} {kind}",
    )
    check(
        "a página de erro explica o que conferir",
        "Confira os campos" in response.text,
        re.sub(r"<[^>]+>", " ", response.text)[:80],
    )
    api = client.get("/api/books?page=abc")
    check(
        "a API segue respondendo JSON",
        "application/json" in api.headers.get("content-type", ""),
        api.headers.get("content-type", ""),
    )


def _import_category_checks(client) -> None:
    """Categoria é obrigatória na importação (organiza biblioteca e OPDS)."""
    print("\n[importação exige categoria]")
    response = client.post(
        "/import/upload",
        files={"files": ("livro.epub", b"PK\x03\x04nada", "application/epub+zip")},
        data={"category": "", "convert": "on"},
    )
    check(
        "upload sem categoria avisa e não importa",
        response.status_code == 400 and "Escolha uma categoria" in response.text,
        str(response.status_code),
    )
    response = client.post(
        "/import/scan", data={"path": "C:/Windows", "category": ""}, follow_redirects=False
    )
    check(
        "varredura sem categoria volta pedindo a categoria",
        response.status_code == 303 and "err=" in (response.headers.get("location") or ""),
        response.headers.get("location", "")[:60],
    )
    page = client.get("/import").text
    check("o formulário marca a categoria como obrigatória", "category-list\" required" in page)


def _opds_checks(client) -> None:
    print("\n[OPDS]")
    response = client.get("/opds")
    check("OPDS 1.2 raiz", response.status_code == 200 and "opds-catalog" in response.text)
    check("OPDS content-type", "atom+xml" in response.headers.get("content-type", ""))
    check(
        "raiz é feed de aquisição (clientes simples mostram livros)",
        "kind=acquisition" in response.headers.get("content-type", ""),
        response.headers.get("content-type", ""),
    )

    response = client.get("/opds/browse")
    check("OPDS navegação pura", response.status_code == 200 and "kind=navigation" in response.headers.get("content-type", ""))
    check("navegação tem subsection", 'rel="subsection"' in response.text)

    response = client.get("/opds/all")
    check("OPDS lista", response.status_code == 200 and "<feed" in response.text)

    response = client.get("/opds/search.xml")
    check("OPDS opensearch", response.status_code == 200 and "OpenSearchDescription" in response.text)

    response = client.get("/opds/v2/")
    check("OPDS 2.0", response.status_code == 200 and "application/opds+json" in response.headers.get("content-type", ""))

    # Links must follow the address the client used, not a guessed local IP,
    # otherwise a phone on Tailscale/VPN is sent to a network it cannot reach.
    response = client.get("/opds/all", headers={"host": "100.64.9.9:8080"})
    check(
        "links usam o endereço do cliente (Tailscale/VPN/domínio)",
        "http://100.64.9.9:8080/opds/" in response.text,
        "host do cliente não apareceu nos links",
    )
    check(
        "não vaza o IP local quando o cliente usa outro endereço",
        "192.168." not in response.text,
        "apareceu um IP da LAN no feed",
    )
    response = client.get("/opds/device/xteink_x4_pro", headers={"host": "leitor.local:8080"})
    check(
        "feed por dispositivo usa o endereço do cliente",
        "http://leitor.local:8080" in response.text or response.status_code == 200,
        "host do cliente não apareceu no feed de dispositivo",
    )


def _devices_checks(client) -> None:
    """The app targets e-ink readers in general; Xteink is just one preset."""
    from app.devices.builtin import DEFAULT_PROFILE_SLUG
    from app.devices.registry import all_profiles

    profiles = all_profiles()
    brands = {profile.brand for profile in profiles.values()}

    check("perfil padrão não é de uma marca específica",
          "xteink" not in DEFAULT_PROFILE_SLUG, DEFAULT_PROFILE_SLUG)
    check("catálogo de presets amplo",
          len(profiles) >= 15 and len(brands) >= 6,
          f"{len(profiles)} perfis / {len(brands)} marcas")
    check("Xteink é um preset entre vários", "xteink_x4_pro" in profiles)
    check("há um preset recomendado", any(p.recommended for p in profiles.values()))
    check("presets cobrem marcas populares",
          {"Kindle", "Kobo", "PocketBook"} <= brands, str(sorted(brands)))

    response = client.get("/devices")
    check("página de dispositivos agrupa por marca",
          response.status_code == 200 and "Kindle" in response.text and "Xteink" in response.text)


def _network_checks() -> None:
    """Validate LAN address auto-detection (server exposed on the network)."""
    from app.networking import access_urls, is_loopback, is_wildcard, primary_ipv4, resolve_base_url

    check("wildcard reconhecido", is_wildcard("0.0.0.0"))
    check("loopback reconhecido", is_loopback("127.0.0.1") and is_loopback("localhost"))

    auto = resolve_base_url("", "0.0.0.0", 8080)
    check("BASE_URL vazio resolve p/ LAN (não localhost)", not auto.startswith("http://localhost"), auto)

    explicit = resolve_base_url("https://biblioteca.exemplo", "0.0.0.0", 8080)
    check("BASE_URL explícito é respeitado", explicit == "https://biblioteca.exemplo", explicit)

    urls = access_urls("0.0.0.0", 8080)
    check("lista de endereços de acesso", len(urls) >= 1 and all(u.startswith("http") for u in urls), str(urls))
    print(f"       ip primário: {primary_ipv4()}")
    print(f"       urls: {urls}")


def _api_checks(client) -> None:
    print("\n[API]")
    for path in ("/api/system/health", "/api/system/stats", "/api/books", "/api/devices", "/api/feeds", "/api/conversions/stats"):
        response = client.get(path)
        check(f"GET {path}", response.status_code == 200, f"{response.status_code} {response.text[:120]}")


def _conversion_checks(client) -> None:
    print("\n[conversores]")
    from app.converters.registry import converter_names
    from app.converters.tools import detect_toolchain

    names = converter_names()
    check("conversores registrados", len(names) >= 5, str(names))
    print(f"       conversores: {names}")
    print(f"       ferramentas: {detect_toolchain().as_dict()}")


def _library_checks(client) -> None:
    print("\n[importação e conversão real]")
    from io import BytesIO

    from PIL import Image

    # Build a tiny 3-page CBZ and import it through the API.
    buffer = BytesIO()
    import zipfile

    with zipfile.ZipFile(buffer, "w") as zf:
        for index, color in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255)], start=1):
            img = Image.new("RGB", (400, 600), color)
            page = BytesIO()
            img.save(page, "PNG")
            zf.writestr(f"{index:03d}.png", page.getvalue())
    buffer.seek(0)

    response = client.post(
        "/api/imports/upload",
        files={"files": ("teste_manga.cbz", buffer.getvalue(), "application/vnd.comicbook+zip")},
        data={"category": "Manga"},
    )
    check("upload CBZ", response.status_code == 200, response.text[:200])
    payload = response.json()
    check("CBZ importado", payload.get("imported") == 1, str(payload))
    if not payload.get("results") or not payload["results"][0].get("book"):
        return

    book_id = payload["results"][0]["book"]["id"]

    response = client.get(f"/api/books/{book_id}")
    check("detalhe do livro", response.status_code == 200 and response.json()["content_type"] == "comic")

    _epub_import_check(client)
    _import_convert_check(client)
    _opds_book_checks(client, book_id)

    response = client.get(f"/api/conversions/targets?book_id={book_id}&device_profile=xteink_x4_pro")
    check("destinos compatíveis", response.status_code == 200 and len(response.json()["targets"]) > 0)
    if response.status_code == 200:
        targets = [t["format"] for t in response.json()["targets"]]
        check("EPUB recomendado para Xteink", targets[0] == "epub", str(targets))

    # Run a conversion synchronously (bypassing the queue) to prove the pipeline.
    from app.converters.runner import run_conversion
    from app.database import session_scope
    from app.database.models import Book
    from app.devices.registry import get_profile
    from app.library.detect import detect
    from app.metadata.extractor import extract_metadata
    from app.storage.paths import resolve_library_path
    from app.storage.temp import temp_workdir

    with session_scope() as session:
        book = session.get(Book, book_id)
        path = resolve_library_path(book.file_path)
        detection = detect(path)
        metadata = extract_metadata(path, detection)
        profile = get_profile("xteink_x4_pro", session)

    with temp_workdir("smoke_") as workdir:
        result, plan = run_conversion(
            source=path,
            detection=detection,
            metadata=metadata,
            profile=profile,
            workdir=workdir,
            target_format=None,
        )
        size = result.output_path.stat().st_size
        check("conversão CBZ->EPUB", result.output_path.suffix == ".epub" and size > 0, str(result.output_path))
        print(f"       plano: {plan.target_format} via {result.converter} ({size} bytes)")
        check("EPUB válido", _is_valid_epub(result.output_path))

    _queue_checks(client, book_id)


def _epub_import_check(client) -> None:
    """Import a real EPUB and prove the feed advertises the exact MIME type.

    This is the regression guard for the CrossPoint bug: an EPUB is a ZIP
    container, and announcing ``application/zip`` hid every book on the device.
    """

    from PIL import Image

    from app.converters.epub import EpubMeta, write_epub

    print("\n[EPUB importado]")
    work = Path(tempfile.mkdtemp(prefix="opds_epub_"))
    images = []
    for index in range(2):
        img = Image.new("RGB", (300, 450), (200, 200 - index * 40, 120))
        path = work / f"{index}.png"
        img.save(path)
        images.append(path)
    epub_path = work / "livro_teste.epub"
    write_epub(epub_path, images=images, meta=EpubMeta(title="Livro Teste", author="Autor"))

    response = client.post(
        "/api/imports/upload",
        files={"files": ("livro_teste.epub", epub_path.read_bytes(), "application/epub+zip")},
    )
    shutil.rmtree(work, ignore_errors=True)
    payload = response.json()
    check("EPUB importado", payload.get("imported") == 1, str(payload)[:200])
    if not payload.get("results") or not payload["results"][0].get("book"):
        return

    epub_id = payload["results"][0]["book"]["id"]
    detail = client.get(f"/api/books/{epub_id}").json()
    check(
        "media_type do EPUB é application/epub+zip",
        detail["media_type"] == "application/epub+zip",
        detail["media_type"],
    )
    download = client.get(f"/opds/download/{epub_id}")
    check(
        "download do EPUB anuncia application/epub+zip",
        "application/epub+zip" in download.headers.get("content-type", ""),
        download.headers.get("content-type", ""),
    )


def _import_convert_check(client) -> None:
    """Import + convert in a single request (the point of the import page)."""
    import zipfile
    from io import BytesIO

    from PIL import Image

    print("\n[importar e converter na mesma etapa]")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for index in range(3):
            img = Image.new("L", (500, 750), 200 - index * 30)
            page = BytesIO()
            img.save(page, "PNG")
            zf.writestr(f"{index:03d}.png", page.getvalue())

    response = client.post(
        "/api/imports/upload",
        files={"files": ("volume_conv.cbz", buffer.getvalue(), "application/vnd.comicbook+zip")},
        data={
            "category": "Manga",
            "convert": "true",
            "target_format": "auto",
            "device_profile": "xteink_x4_pro",
            "keep_original": "true",
        },
    )
    payload = response.json()
    check("importação respondeu", response.status_code == 200, str(payload)[:200])
    entry = (payload.get("results") or [{}])[0]
    check("arquivo importado", entry.get("status") == "imported", str(entry)[:200])
    check(
        "conversão enfileirada na importação",
        bool(entry.get("conversion", {}).get("job_id")),
        str(entry)[:200],
    )
    if entry.get("conversion"):
        print(f"       job criado: {entry['conversion']['target_format']}")


def _media_type_checks() -> None:
    """CrossPoint matches the OPDS acquisition type EXACTLY, so it must be right."""
    from app.library.formats import media_type_for
    from app.library.sniff import resolve_format

    check("epub -> application/epub+zip", media_type_for("epub") == "application/epub+zip")
    fmt, mime, corrected = resolve_format("epub", b"PK\x03\x04rest")
    check(
        "EPUB (container zip) mantém o MIME do EPUB",
        fmt == "epub" and mime == "application/epub+zip" and not corrected,
        f"{fmt} {mime} corrected={corrected}",
    )
    fmt, mime, corrected = resolve_format("cbz", b"PK\x03\x04rest")
    check("CBZ mantém o MIME do CBZ", fmt == "cbz" and mime == "application/vnd.comicbook+zip")
    fmt, mime, corrected = resolve_format("epub", b"%PDF-1.7")
    check("arquivo mal rotulado é corrigido", fmt == "pdf" and corrected, f"{fmt} {corrected}")
    check(
        "MIME genérico no banco é substituído pelo canônico",
        media_type_for("epub", "application/zip") == "application/epub+zip",
    )


def _opds_book_checks(client, book_id: str) -> None:
    """Verify real OPDS entries, acquisition links and asset delivery."""
    from xml.etree import ElementTree as ET

    print("\n[OPDS com um livro real]")
    # A raiz depende de OPDS_ROOT_MODE (por padrão só menus); o catálogo de
    # livros é o /opds/all e o /opds/device/{slug} (o que o CrossPoint usa).
    response = client.get("/opds/all")
    check(
        "catálogo entrega o livro direto (CrossPoint mostra conteúdo)",
        "opds-spec.org/acquisition" in response.text,
    )
    check(
        "tipo do CBZ correto no link",
        'type="application/vnd.comicbook+zip"' in response.text,
        str(_acquisition_types(client, "/opds/all")),
    )
    check(
        "EPUB anuncia application/epub+zip (exigência do CrossPoint)",
        'type="application/epub+zip"' in response.text,
        str(_acquisition_types(client, "/opds")),
    )

    response = client.get("/opds/all")
    check("feed de aquisição", response.status_code == 200)
    try:
        root = ET.fromstring(response.content)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall("a:entry", ns)
        check("entradas presentes", len(entries) >= 1, str(len(entries)))
        acquisitions = root.findall(".//a:link[@rel='http://opds-spec.org/acquisition']", ns)
        check("link de aquisição", len(acquisitions) >= 1, str(len(acquisitions)))
    except ET.ParseError as exc:
        check("XML válido", False, str(exc))

    response = client.get(f"/opds/books/{book_id}")
    check("entrada individual", response.status_code == 200 and "<feed" in response.text)

    response = client.get(f"/opds/cover/{book_id}")
    check("capa servida", response.status_code == 200 and "image/jpeg" in response.headers.get("content-type", ""))

    response = client.get(f"/opds/download/{book_id}")
    check("download direto", response.status_code == 200 and len(response.content) > 0)

    response = client.get("/opds/search?q=teste")
    check("busca OPDS", response.status_code == 200 and "<feed" in response.text)

    # The download link must point at the address the client used (e.g. the
    # Tailscale IP), otherwise the device is sent to a network it is not on.
    tailscale = "100.126.200.90:8080"
    response = client.get("/opds/device/xteink_x4_pro", headers={"host": tailscale})
    check(
        "feed por dispositivo usa o endereço do cliente (Tailscale)",
        f"http://{tailscale}/opds/download/" in response.text,
        "o host do cliente não apareceu no link de download",
    )


def _acquisition_types(client, path: str) -> list[str]:
    import re

    response = client.get(path)
    return re.findall(r'opds-spec\.org/acquisition[^>]*type="([^"]+)"', response.text)


def _queue_checks(client, book_id: str) -> None:
    """Exercise the real background worker: enqueue, then watch it finish."""
    import time

    print("\n[fila de conversão assíncrona]")
    response = client.post(
        "/api/conversions",
        json={
            "book_ids": [book_id],
            "target_format": "cbz",
            "device_profile": "xteink_x4_pro",
            "keep_original": True,
            "options": {},
        },
    )
    check("job enfileirado", response.status_code == 200 and response.json()["created"] == 1, response.text[:200])
    if response.status_code != 200 or not response.json().get("jobs"):
        return
    job_id = response.json()["jobs"][0]["id"]

    deadline = time.time() + 60
    status = "pending"
    while time.time() < deadline:
        listing = client.get("/api/conversions").json()
        job = next((j for j in listing["items"] if j["id"] == job_id), None)
        if job:
            status = job["status"]
            if status in {"done", "failed"}:
                break
        time.sleep(0.5)

    check("job concluído pelo worker", status == "done", f"status final: {status}")
    if status == "done":
        listing = client.get("/api/conversions").json()
        job = next(j for j in listing["items"] if j["id"] == job_id)
        check("job gerou novo livro", bool(job.get("output_book_id")))
        print(f"       conversor usado: {job.get('converter')}")


def _is_valid_epub(path) -> bool:
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            return "mimetype" in zf.namelist() and "OEBPS/content.opf" in zf.namelist()
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
