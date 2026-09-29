# Desenvolvimento

Guia para quem vai mexer no código. Para a visão geral, veja o
[README](../README.md); para o mapa de módulos e invariantes, veja o
[AGENTS.md](../AGENTS.md).

## Preparar o ambiente

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env       # Windows: copy .env.example .env
```

Para a suíte de testes, deixe o `DATA_DIR` apontando para uma pasta temporária —
os scripts já fazem isso sozinhos, mas em testes manuais evite apontar para a sua
biblioteca real.

## Rodar e depurar

```bash
python -m app                       # produção local
uvicorn app.main:app --reload       # com auto-reload
ruff check app tests                # lint
```

O log estruturado sai na saída padrão; defina `LOG_LEVEL=DEBUG` e/ou
`LOG_JSON=true` conforme necessário.

> **Sempre reinicie o processo** depois de editar Python: o servidor em execução
> mantém os módulos em memória.

## Testes

Cada arquivo em `tests/` é um script independente com uma função `check(...)`; não
há pytest. Rode o específico da área e, ao final, o conjunto todo:

```bash
python tests/smoke.py             # boot, rotas, upload, fila, importação
python tests/conversions.py       # pipelines de conversão
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
python tests/covers.py            # capa tipográfica (fonte embutida, acentos)
python tests/schema.py            # migração aditiva (colunas novas em banco antigo)
python tests/downloads.py         # rastreio de downloads (entrega, Range, estados)
```

Os testes sobem o app com `TestClient` e um `DATA_DIR` temporário — nunca tocam
na biblioteca real.

## Adicionar um conversor

1. Crie a estratégia em `app/converters/strategies/` herdando de
   `converters/base.py` (`ConversionStrategy`).
2. Implemente `can_handle(request)` (diga para quais origem/destino serve) e
   `run(request)` (o trabalho em si). Declare `priority` — maior vence.
3. Registre a classe em `app/converters/strategies/__init__.py`.
4. Se a estratégia só fizer sentido com certa ferramenta opcional, consulte
   `converters/capabilities.py` e falhe com mensagem clara.
5. Adicione um caso em `tests/conversions.py` (ou `native_formats.py`) cobrindo
   ida e volta, quando fizer sentido.

Se precisar escolher o destino automaticamente, ajuste `converters/planner.py`.
Se for um formato de **saída** novo, acrescente ao `converters/catalog.py`.

## Adicionar um formato de entrada

1. Registre extensão, MIME e assinaturas em `app/library/formats.py`.
2. Se a detecção por conteúdo exigir algo novo, ajuste `library/sniff.py` ou
   `library/detect.py`.
3. Se o formato tiver metadados próprios, crie um extrator em
   `app/metadata/` e ligue em `metadata/extractor.py`.
4. Cubra com um teste (detecção + importação).

## Adicionar um dispositivo

- **Embutido**: acrescente o preset em `app/devices/builtin.py` (tela, cor,
  níveis de cinza, spreads, qualidade, formato de imagem). O seed roda no boot.
- **Pelo painel**: `/devices` → criar perfil; perfis personalizados sobrescrevem
  os embutidos de mesmo slug.
- Documente as fontes/constantes em `docs/device-formats.md`.

## Adicionar uma rota do painel

1. Novo módulo em `app/web/routes/` com um `APIRouter`.
2. Template em `app/web/templates/` herdando de `base.html`.
3. Inclua o router em `build_web_router()` (`app/web/routes/__init__.py`).
4. Adicione o link de navegação em `templates/base.html`.
5. Erros amigáveis: deixe o handler de `app/web/errors.py` converter exceções em
   flash legível; para validação no formulário, re-renderize com `err` no
   contexto (o `base.html` aceita `ok`/`err` do contexto **ou** da query string).

## Adicionar uma rota da API

1. Novo módulo em `app/api/` com o router e schemas em `app/api/schemas.py`.
2. Inclua em `build_api_router()` (`app/api/__init__.py`).
3. A documentação sai sozinha em `/api/docs`.

## Adicionar uma rota OPDS

- **Seções/navegação**: `app/opds/v1/navigation.py`.
- **Livros/aquisição**: `app/opds/v1/acquisition.py`.
- **Entrada/capa/arquivo de um livro**: `app/opds/v1/assets.py`.

Mantenha os MIME/types em `opds/constants.py` e siga o helper de construção de
feeds (`opds/atom.py`, `opds/feeds.py`). Lembre que **clientes simples só listam
entradas com link de download**.

## Banco de dados e reparos

- Modelos em `app/database/models/`; o engine (SQLite WAL) e `session_scope`
  ficam em `app/database/base.py`.
- Não há framework de migração. Mudanças leves e idempotentes (preencher campos,
  corrigir caminhos, migrar MIME antigo) entram em **`app/library/repairs.py`**,
  que roda no boot e pela manutenção do painel.
- Nunca guarde caminho absoluto no banco: use o caminho relativo à biblioteca.

## Fila e workers

- A fila é a tabela `conversion_jobs`. O claim é um `UPDATE` condicional
  (`workers/queue.py`); jobs `running` sem heartbeat voltam a `pending` no boot.
- O trabalho pesado roda em `asyncio.to_thread`; o event loop continua servindo
  web/OPDS.
- **Cancelamento cooperativo**: verifique `is_cancelled(job_id)` dentro de loops
  longos e levante `JobCancelled` (`workers/progress.py`). Não capture essa
  exceção genericamente.
- Loops em background: `conversion_loop.py`, `rss_loop.py`, `maintenance.py`,
  orquestrados por `workers/manager.py`.

## Estilo

- `from __future__ import annotations`; tipos modernos; linhas até 100 colunas.
- Comentários/docstrings **em inglês**; interface **em pt-BR**.
- Um módulo, uma responsabilidade. Prefira extrair a inchar.
- Sem CDNs: CSS/JS locais em `app/web/static`, com `static_url()` para cache.
- Sem dependência nova que exija binário do sistema.
- Rode `ruff check app tests` antes de encerrar.

## Arte e imagens (banner, badges, diagrama)

A identidade visual é **gerada**, não desenhada à mão:

```bash
# edite os textos/versão aqui
$EDITOR tools/artwork/artwork.toml
.\.venv\Scripts\python.exe -m tools.artwork.generate
```

- Cores e fontes vêm de `app/web/static/style.css`; os ícones, do partial do
  painel; a versão, de `app/__init__.py`. Não duplique nenhum desses valores.
- `--check` falha se a arte versionada estiver desatualizada, e
  `python tests/artwork.py` valida XML, dimensões, autocontenção e deriva.
- Avisos de "texto não cabe" são para **resolver** (encurtar/reduzir), nunca
  ignorar. Detalhes e guia de extensão: [`tools/artwork/README.md`](../tools/artwork/README.md).
- Capturas de tela do painel não são geradas: veja o processo em
  [`docs/panel.md`](panel.md) (Chrome headless + instância temporária).

## Containers (RAM e disco)

Antes de mexer em `Dockerfile`/`docker-compose.yml`, **meça**:

```bash
python tools/devops/container_report.py                 # imagem + container do compose
python tools/devops/container_report.py --json          # resumo para comparar antes/depois
```

O playbook (imagem, runtime e disco), os guardrails e a linha de base medida
estão em [`docs/devops/containers.md`](devops/containers.md). Para uma auditoria
completa, use o agente `container-optimizer` (`/container-audit`), que segue o
método medir → diagnosticar → aplicar → verificar → relatar.

## Documentação

Ao mudar comportamento visível, atualize o documento correspondente em `docs/`
(`opds.md`, `conversion.md`, `device-formats.md`, `rss.md`, `panel.md`,
`architecture.md`, `devops/containers.md`) e, se for uma regra estrutural, o
`AGENTS.md`.
