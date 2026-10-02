# AGENTS.md — guia para agentes de IA

Contexto de engenharia deste repositório. Leia antes de editar. O público-alvo
são **agentes de código**; para humanos veja o [README](README.md).

## 1. O que é

Servidor pessoal **OPDS 1.2/2.0** para leitores e-ink, com painel web,
biblioteca em `DATA_DIR`, fila de conversão persistente e ingestão RSS/Atom.
Monólito modular FastAPI + SQLite + SQLAlchemy + Jinja2. **Um processo.**

Regras de ouro do projeto:

1. **Nenhuma ferramenta externa é necessária.** Conversões usam só Python
   (`Pillow`, `PyMuPDF`, `lxml`, `py7zr`, `python-docx`, `rarfile`) + a
   `unrar.dll` em `app/vendor/unrar` (**Windows**; fora dele o CBR/RAR usa o
   `bsdtar`/`unrar` do sistema via `rarfile`, e o painel informa o que existe).
   Calibre/Ghostscript/ImageMagick/FFmpeg são **opcionais** e nunca podem virar
   requisito. Não adicione dependência que
   exija binário do sistema.
2. **Nunca dependa de internet/CDN.** CSS/JS são locais (`app/web/static`), sem
   `fonts.googleapis.com` etc. URLs de assets passam por `static_url()` para
   versionamento de cache.
3. **UI e mensagens em pt-BR; comentários/docstrings em inglês.** Nomes de
   código em inglês.
4. **Arquivos pequenos, uma responsabilidade cada.** Prefira criar módulo novo a
   inchar um existente. O projeto já é bem subdividido — siga o padrão da pasta.
5. **Auditoria não muda regra de negócio.** Antes de remover algo, entenda para
   que serve e verifique se nada mais usa.

## 2. Comandos

```bash
# rodar o servidor (usa .env; DATA_DIR=./data por padrão)
.\.venv\Scripts\python.exe -m app          # Windows
python -m app                               # Linux/macOS

# lint (inclui as ferramentas de desenvolvimento)
.\.venv\Scripts\ruff.exe check app tests tools

# ferramentas de desenvolvimento
.\.venv\Scripts\python.exe tools\devops\container_report.py   # mede containers (imagem, camadas, RAM)
.\.venv\Scripts\python.exe -m tools.artwork.generate          # regenera a arte (banner, badges, diagrama)
.\.venv\Scripts\python.exe -m tools.artwork.generate --check  # falha se a arte estiver desatualizada

# testes (cada arquivo é um script independente; usa DATA_DIR temporário)
python tests/smoke.py
python tests/conversions.py
python tests/native_pdf.py
python tests/native_formats.py
python tests/kindle_writer.py
python tests/crosspoint_compat.py
python tests/reader.py
python tests/rss_feeds.py
python tests/sitemap.py
python tests/archives.py
python tests/frontend.py
python tests/cancellation.py
```

Windows: `.\tasks.ps1 run|test|smoke|convert|rss|crosspoint|clean`.
Linux/macOS: `make run|test|smoke|convert|crosspoint|clean`.

### Fluxo de verificação esperado

Ao terminar uma mudança: `ruff check app tests` **e** rode pelo menos
`tests/smoke.py` + o suite da área tocada. Se mexeu em OPDS, rode
`tests/crosspoint_compat.py`; se mexeu em conversão, `tests/conversions.py` +
`tests/native_formats.py`; se mexeu em importação/painel, `tests/smoke.py` +
`tests/frontend.py`.

**Pegadinha:** o servidor em execução carrega os módulos **na memória**. Depois
de editar Python, **reinicie o processo** antes de validar no navegador/OPDS —
senão você testa código velho. Testes que sobem `TestClient` carregam o código
novo.

## 3. Mapa do código

```
app/
├── main.py            create_app(), lifespan (init_db, seed, repairs, workers)
├── config.py          Settings pydantic (.env) + caminhos derivados
├── logging_conf.py    log texto/JSON
├── networking.py      resolução de BASE_URL e IPs de acesso
├── request_context.py base URL por requisição (contextvar)
│
├── database/          engine SQLite (WAL) + session_scope + modelos ORM
│   └── models/        book, classification, jobs, feeds, system, enums
│
├── storage/           paths.py (nomes seguros/relativos), temp.py, usage.py
│
├── downloads/         rastreio de downloads: store.py (domínio),
│                      response.py (entrega instrumentada) e cleanup.py
│                      (limpeza global + retenção por feed)   <-- ver docs/downloads.md
│
├── library/           domínio biblioteca
│   ├── formats.py     taxonomia extensão↔MIME↔assinatura   <-- novo formato aqui
│   ├── sniff.py       magic bytes
│   ├── detect.py      detecção em camadas
│   ├── importer.py    importação + dedup SHA-256 + capa
│   ├── scanner.py     varredura de pasta
│   ├── repository.py  consultas/filtros/facetas
│   ├── taxonomy.py    categorias e tags
│   ├── service.py     editar/renomear/mover/excluir/capa
│   ├── repairs.py     reparos idempotentes (rodam no boot)  <-- migrações leves
│   └── conversions.py enqueue_conversion()/link_conversion()
│
├── metadata/          extratores por formato + cover.py (gera capa)
│
├── converters/        pipeline
│   ├── capabilities.py  o que esta instalação consegue fazer
│   ├── catalog.py       formatos de saída compatíveis
│   ├── planner.py       decide destino + opções (auto)
│   ├── registry.py      escolhe a estratégia
│   ├── runner.py        executa o plano (roda em thread)
│   ├── manga/           textos de mangá: geometria + OCR opcional (Pillow/RapidOCR)
│   ├── collections/images/pdf: collectors, imageops, pdf_render, normalise
│   ├── archives.py + unrar_dll.py   ZIP/TAR/7z/RAR
│   ├── epub/            escritor E leitor de EPUB próprios
│   ├── native/          reflow de PDF (pdf_text), escrita de PDF (pdf_writer),
│   │                    KF8/MOBI (kindle)
│   ├── webpage/         HTML → EPUB (dom, main_content, images, split…)
│   └── strategies/      conversores concretos  <-- novo conversor aqui
│
├── devices/           builtin.py (presets) + profile.py + registry.py
├── opds/              v1/ (navigation, acquisition, assets) + v2.py + entries/feeds
├── rss/               parser, downloader, service (política de fonte), sitemap
├── reader/            leitor web: registry + epub/pdf/comic/text/fb2 + sanitize
├── workers/           queue.py (claim no banco), conversion_loop, rss_loop,
│                      maintenance, manager, progress.py (cancelamento)
├── security/          passwords, auth (sessão + Basic), settings_store
├── api/               REST /api (books, uploads, conversions, devices, feeds, system)
├── tools/             CLIs internas (ex.: import_site)
└── web/               errors.py, labels.py, templating.py
    ├── routes/        auth, dashboard, library, imports, conversions,
    │                  devices, feeds, reader, settings  <-- nova página aqui
    └── templates/ + static/  (base.html, style.css, app.js, reader.*)

Fora do pacote da aplicação (ferramentas e configuração de agentes):

.opencode/
├── agents/            container-optimizer.md   (DevOps: otimiza containers)
└── commands/          container-audit.md       (/container-audit)
tools/
├── artwork/           banner, cartão social, badges e diagrama (SVG declarativo)
└── devops/            container_report.py      (mede imagem, camadas e RAM)
```

## 4. Invariantes (não quebre sem entender)

- **OPDS raiz configurável.** `OPDS_ROOT_MODE ∈ {mixed, navigation, books}`
  (`config.py`). O padrão é `mixed` *no código*, mas o `.env` do usuário pode
  fixar outro. Testes que dependem da raiz devem ser **cientes do modo**
  (ver `tests/smoke.py::_opds_root_checks`).
- **Clientes simples** (CrossPoint/Xteink) só listam entradas com link de
  download — por isso existem `/opds/device/{slug}`, `/opds/all` e `/opds/new`
  como catálogos de aquisição independentes do modo da raiz.
- **Livro sem autor não emite `<author>`** na entrada (o feed declara autor no
  nível do feed) para o leitor nomear o arquivo pelo título.
- **Categoria é obrigatória na importação** (upload/varredura no painel): o
  formulário marca `required` e a rota valida no servidor. Feeds criam
  `rss/<nome do feed>` automaticamente.
- **Feed tem um formulário só.** `partials/feed_form.html` desenha os campos e é
  usado por criar (`/feeds`) e editar (`/feeds/<id>/edit`); o `POST /feeds/save`
  cria ou atualiza conforme o `feed_id` — não duplique os campos num template
  novo. `Feed.url` é única: o save checa antes (o commit estouraria um
  `IntegrityError`), e números de formulário são lidos como texto (`_as_int`)
  para campo vazio não virar 422. Mudar nome/subcategoria só afeta os itens
  novos; os livros antigos são reposicionados por `apply_feed_categories`.
- **Retenção por feed.** `Feed.cleanup_enabled`/`cleanup_days` (formulário do
  feed) removem posts mais velhos que N dias, independente da limpeza global —
  `app/downloads/cleanup.py` (`FeedRetentionRule`, `plan_feed`, `run_feed`,
  `run_feeds`), chamado na manutenção. O `FeedItem` **fica** (sem `book_id`):
  é o que impede o post de voltar na próxima busca. Nunca remova um post com
  download em curso ou conversão na fila (`_idle`); a remoção passa por
  `library/service.delete_books` e o `file_records` vira `deleted`.
- **Um formato por arquivo = um `Book`.** Conversão gera um novo `Book` ligado ao
  origem por `origin_book_id`; o OPDS agrupa como variantes do mesmo título.
- **Imagens de EPUB convertido** apontam para `../images/` (ver
  `epub/text_builder._fix_image_sources` e `repairs.repair_epub_image_paths`).
  Se mexer em escrita de EPUB, verifique imagens no leitor **e** no Web Reader.
- **Caminhos de arquivo são relativos** à biblioteca (`Book.file_path`). Nunca
  guarde caminho absoluto no banco. Resolva via `storage/paths.py`.
- **Sessão de banco não cruza threads.** Leitura/escrita de ORM só na thread
  principal; a thread de conversão recebe dados puros e o callback de progresso
  abre a própria sessão.
- **Fila = tabela `conversion_jobs`.** Claim é `UPDATE` condicional; jobs
  `running` sem heartbeat voltam a `pending` no boot (`startup_recovery`).
- **Cancelamento** usa `JobCancelled(BaseException)` (`workers/progress.py`) e
  `is_cancelled`/`note_cancelled` (`workers/queue.py`). Não engula essa exceção.
- **MIME/types** saem de `library/formats.py`; não invente string solta.
- **Nada de API só-de-plataforma no import.** O painel importa muita coisa ao
  renderizar (ex.: a sondagem de backends de arquivo), então um módulo que toca
  `ctypes.WINFUNCTYPE`, `winreg` ou caminhos do Windows no nível do módulo
  derruba a página no Linux. Resolva essas APIs **dentro da função**, atrás de
  uma checagem de plataforma, e faça a sondagem de capacidade **nunca lançar**.
- **Capacidade é sempre best-effort**: `available_backends()` e afins devolvem o
  que existe em vez de explodir; o que falta simplesmente aparece como
  indisponível na interface.
- **Banco cifrado (SQLCipher)**: o engine usa o dialeto `sqlite+pysqlcipher`
  (`database/sqlcipher.py`) porque o mapeamento de exceções do SQLAlchemy segue
  o DBAPI do dialeto — com um `creator` de `sqlcipher3`, `IntegrityError`
  escaparia. A chave (`DATA_DIR/secret.key`, 0600) é aplicada **antes de
  qualquer outra coisa** em cada conexão (`database/crypto.py`). Um banco em
  texto puro é migrado no boot via `sqlcipher_export` (com
  `opds.db.plain.bak`); `PRAGMA rekey` não funciona nesse sentido. Nunca
  versione a `secret.key` nem quebre a ordem "chave primeiro".
- **Configuração vive no banco, não no `.env`.** `security/runtime.py` descreve
  cada ajuste (`FIELDS`: rótulo, tipo, limites) e mantém um cache; o assistente
  (`/setup`) e a tela de Configurações são gerados do mesmo registro — não
  duplique rótulo ou validação em template. Na tela de Configurações cada grupo
  de `FIELDS` vira uma **seção expansível** (`<details data-sect>`) e os campos
  `advanced` vão para o bloco «Avançado» da própria seção; o título vem de
  `GROUPS` e no template ficam só o ícone e a descrição (apresentação). Tudo que
  é configurável mora em **um único** `<form action="/settings/app">` — seção
  recolhida continua sendo enviada, então não mova um campo desse form sem
  acertar o `rendered`. `get_settings()` devolve um
  **overlay** (`security/effective.py`) com o banco por cima do `.env`, então
  todo o app já respeita o banco sem mudar call sites; para ler o valor cru do
  `.env` use `get_base_settings()`. Ao salvar um formulário, informe em
  `runtime.save(..., rendered=...)` **quais campos a tela mostrou**
  (`runtime.field_names(onboarding=True)` no assistente, `field_names()` em
  Configurações): caixa renderizada e ausente vira `False`, e campo que não
  estava na tela fica intocado. Sem isso, um formulário parcial apaga o que o
  usuário não viu (já aconteceu: desligava o login do painel).
- **Assistente de primeiro acesso**: o middleware em `main.py` manda o painel
  para `/setup` enquanto a credencial não tiver sido definida **no painel**
  (`auth.change_password` grava `admin_confirmed`). Isso cobre instalação nova
  *e* instalação antiga cuja senha vinha do `.env` — que passa pelo assistente
  uma vez. A API responde 503; `/opds`, `/health` e `/static` continuam
  acessíveis. O `.env` **não cria mais** admin (`ADMIN_USERNAME`/
  `ADMIN_PASSWORD` são ignorados). A senha do OPDS é guardada como **hash** e
  verificada em `auth.require_opds_auth`.

## 5. Onde mexer

| Quero… | Vá em… |
| --- | --- |
| Novo formato de entrada | `library/formats.py` (+ `sniff.py` se precisar) |
| Novo conversor | subclasse em `converters/strategies/`, registre em `strategies/__init__.py`; declare `can_handle` e `priority` |
| Novo motor (ex. render) | `converters/native/` |
| Ajustar escolha automática | `converters/planner.py` |
| Ampliar/reescrever texto de mangá/quadrinhos | `converters/manga/` (`detector` → `layout` → `enlarge`/`reflow`; OCR opcional em `ocr.py` + `relayout.py`) |
| Formato de saída por dispositivo | `devices/builtin.py` (+ `converters/catalog.py`) |
| Nova página do painel | `web/routes/*.py` + template + incluir em `build_web_router()`; adicione link em `templates/base.html` |
| Nova rota da API | `api/*.py` + incluir em `build_api_router()` + schema em `api/schemas.py` |
| Nova rota OPDS | `opds/v1/navigation.py` (seções) ou `acquisition.py` (livros) |
| Novo endpoint do leitor | `reader/registry.py` + handler em `reader/` |
| Reparo/migração leve | `library/repairs.py` (idempotente, roda no boot) |
| Rastrear downloads / gerir arquivos | `app/downloads/` (`store.py` = domínio, `response.py` = entrega) — ver `docs/downloads.md` |
| CLI interna | `app/tools/` |
| Otimizar containers | `.opencode/agents/container-optimizer.md` + `tools/devops/container_report.py` (ver `docs/devops/containers.md`) |
| Arte/documentação visual (banner, badges, diagrama) | `tools/artwork/` — edite `artwork.toml` e rode `python -m tools.artwork.generate` |

## 6. Convenções de código

- `from __future__ import annotations` no topo; tipos modernos (`list[str]`,
  `X | None`).
- `ruff` com `line-length=100`, regras `E,F,W,I,UP,B,C4,SIM` (`E501`/`B008`
  ignoradas). Mantenha o lint limpo.
- Docstrings/comentários **em inglês**, curtas e explicando o *porquê*.
- Texto de interface **em pt-BR**, direto, sem jargão. Mensagens de erro devem
  dizer o que fazer (o painel converte erros em flash legível via `web/errors.py`).
- Templates Jinja herdam de `base.html`; flashes de sucesso/erro usam
  `?ok=`/`?err=` **ou** contexto `ok`/`err` (base.html aceita os dois).
- Nunca use `print` para log; use `logging`.
- Testes são scripts com `check(...)` (sem pytest); siga o padrão do arquivo.

## 7. Armadilhas conhecidas

- **Reinicie o app** depois de editar Python (código em memória).
- `/opds/all?per_page=` tem teto **100** (acima disso → 422); pagine.
- EPUB→PDF: caminhos do OPF são **relativos ao OPF** — bug já visto gerando PDFs
  vazios; use as helpers existentes.
- PDF texto: figuras/tabelas dependem da **legenda** ("Figura/Figure" → imagem;
  "Tabela/Table" → `<table>` com fallback de imagem). Equações com MathML/LaTeX
  são aceitas como *out of scope* (fallback de imagem).
- Upload via **painel** (`/import/upload`) exige categoria; a rota **API**
  (`/api/imports/upload`) não exige (usada por testes/integrações).
- Vídeo/podcast em feed é **ignorado sem erro** — não é falha.

## 8. Onde procurar mais

- Arquitetura e decisões: [`docs/architecture.md`](docs/architecture.md)
- Painel: [`docs/panel.md`](docs/panel.md)
- OPDS: [`docs/opds.md`](docs/opds.md)
- Conversão e formatos: [`docs/conversion.md`](docs/conversion.md),
  [`docs/device-formats.md`](docs/device-formats.md)
- Feeds: [`docs/rss.md`](docs/rss.md)
- Rastreamento de downloads e gerência de arquivos: [`docs/downloads.md`](docs/downloads.md)
- Fluxo de dev (adicionar conversor/dispositivo/rota): [`docs/development.md`](docs/development.md)
- Containers (auditoria de RAM e disco, agente DevOps): [`docs/devops/containers.md`](docs/devops/containers.md)
- Arte do projeto (banner, badges, diagrama): [`tools/artwork/README.md`](tools/artwork/README.md)
