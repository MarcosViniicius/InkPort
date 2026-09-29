# Arquitetura

![Diagrama de arquitetura](images/architecture.svg)

O projeto é um monólito modular: um único processo FastAPI com workers
assíncronos leves. Não há microserviços, fila externa nem banco externo — tudo
roda bem em um VPS barato.

```
app/
├── main.py            cria o app, liga rotas e workers (lifespan)
├── config.py          Settings tipadas (.env)
├── logging_conf.py    log estruturado (texto ou JSON)
│
├── database/          engine, sessão e ORM
│   ├── base.py        engine SQLite (WAL, busy_timeout) e session_scope
│   └── models/        Book, Category/Tag, ConversionJob, Feed, Setting...
│
├── storage/           layout de arquivos e espaço
│   ├── paths.py       nomes seguros, caminhos relativos, resolução
│   ├── temp.py        diretórios de trabalho com limpeza garantida
│   └── usage.py       uso de disco e checagem de limite
│
├── library/           domínio "biblioteca"
│   ├── formats.py     taxonomia de extensões/MIME/assinaturas
│   ├── sniff.py       magic bytes
│   ├── detect.py      detecção em camadas
│   ├── detection.py   objeto Detection
│   ├── containers.py  helpers de ZIP/CBZ
│   ├── importer.py    importação, dedup por hash, capa
│   ├── scanner.py     varredura de pastas
│   ├── repository.py  consultas (busca, filtros, facetas)
│   ├── taxonomy.py    categorias e tags
│   ├── service.py     editar, renomear, mover, excluir, capa
│   ├── conversions.py enfileirar conversão e ligar o resultado ao original
│   └── repairs.py     reparos idempotentes (rodam no boot e na manutenção)
│
├── metadata/          extração de metadados e capas
│   ├── epub.py / pdf.py / comicinfo.py / mobi.py
│   ├── filenames.py   heurística de nome (série/volume/autor)
│   ├── model.py       BookMetadata
│   ├── extractor.py   orquestra e mescla
│   └── cover.py       gera capa para qualquer formato
│
├── converters/        camada de conversão modular (100% Python)
│   ├── base.py        interface e erros
│   ├── capabilities.py o que esta instalação consegue fazer (+ opcionais)
│   ├── tools_optional.py aceleradores externos, se existirem (nunca exigidos)
│   ├── imageops.py    primitivas Pillow
│   ├── collectors.py  extrai páginas (CBZ/CBR/PDF/EPUB)
│   ├── archives.py    ZIP/TAR/7z/RAR (py7zr + unrar.dll do projeto)
│   ├── unrar_dll.py   UnRAR via ctypes sobre a DLL distribuída
│   ├── pdf_render.py  PDF -> imagens (PyMuPDF)
│   ├── normalise.py   redimensiona/recorta/cinza por perfil
│   ├── epub/          escritor E leitor de EPUB próprios
│   ├── native/        motores nativos: reflow de PDF, escrita de PDF, KF8/MOBI
│   ├── strategies/    conversores concretos (EPUB/KEPUB/TXT/FB2/DOCX/Kindle…)
│   ├── calibre_converter.py  opcional, último recurso para formatos exóticos
│   ├── catalog.py     formatos de saída compatíveis
│   ├── planner.py     escolhe destino + opções (auto)
│   ├── registry.py    escolhe o conversor
│   └── runner.py      executa o plano
│
├── devices/           perfis de dispositivos extensíveis (builtin + registry)
├── opds/              OPDS 1.2 (v1/: navigation, acquisition, assets) e 2.0
├── rss/               parser, downloader, política de fonte e sitemap
├── reader/            leitor web (EPUB/PDF/CBZ/texto): registry + handlers
├── workers/           fila persistente + loops (conversão, RSS, manutenção)
├── security/          senhas, sessão do painel, Basic auth do OPDS
├── api/               REST interna (/api): books, uploads, conversions…
├── tools/             CLIs internas (ex.: import_site.py)
└── web/               painel Jinja2 + estáticos
    └── routes/        auth, dashboard, library, imports, conversions,
                       devices, feeds, reader, settings
```

## Fluxo de uma conversão

```
POST /api/conversions
      │  cria ConversionJob(status=pending)
      ▼
ConversionLoop (N instâncias)         workers/conversion_loop.py
      │  claim_next()  (UPDATE ... WHERE status='pending')
      ▼
execute_job                           workers/conversion.py
      │  build_context()  -> lê Book, detecta, carrega perfil
      │  asyncio.to_thread(run_conversion)   <-- não bloqueia o event loop
      ▼
run_conversion                        converters/runner.py
      │  planner.plan_conversion()  decide destino/opções
      │  registry.select_converter()  escolhe a estratégia
      ▼
converter.run(request)                Pillow / PyMuPDF / lxml / zipfile ...
      │  progress via callback throttled -> atualiza o job
      ▼
stage_conversion_file() + link_conversion()   copia para a biblioteca,
      │                                          cria um novo Book e capa
      ▼
queue.finish()  -> status=done, output_book_id
```

## Decisões importantes

- **Fila no banco.** A própria tabela `conversion_jobs` é a fila. Um claim é um
  `UPDATE` condicional, então dois workers nunca pegam o mesmo job.
- **Retomada.** Jobs em `running` sem heartbeat voltam para `pending` no boot
  (`startup_recovery`) e na manutenção periódica.
- **Threads, não bloqueio.** Conversões rodam em `asyncio.to_thread`; o event
  loop segue servindo web/OPDS durante uma conversão.
- **Sessão de banco não cruza threads.** Toda leitura/escrita de ORM acontece na
  thread principal; a thread de conversão só recebe caminhos e dados puros, e o
  callback de progresso abre a própria sessão.
- **OPDS serve direto.** Cada `Book` guarda um caminho relativo à biblioteca;
  o OPDS faz streaming do arquivo, sem duplicar nada.
- **Conversão é conteúdo de primeira classe.** Um arquivo convertido vira um
  novo `Book` ligado ao original (`origin_book_id`). O OPDS agrupa os dois como
  variantes do mesmo título.
- **Nenhuma ferramenta externa é necessária.** Todas as conversões usam pacotes
  Python (Pillow, PyMuPDF, lxml, py7zr, python-docx) e a biblioteca UnRAR
  redistribuída em `app/vendor/unrar` para CBR/RAR. Se a máquina já tiver
  Calibre, ele é usado apenas para formatos exóticos (`.lit`, `.odt`, `.doc`).
- **Tudo relativo ao `DATA_DIR`.** Banco, biblioteca, inbox, temporários e capas
  ficam sob uma única pasta — backup é copiar a pasta.

## Modelo de dados (resumo)

| Tabela | Papel |
| --- | --- |
| `books` | um registro por arquivo (original ou convertido) |
| `categories` / `tags` / `book_tags` | classificação |
| `conversion_jobs` | fila persistente de conversão |
| `feeds` / `feed_items` | feeds RSS/Atom e histórico/dedup de itens |
| `device_profiles` | perfis personalizados (sobrescrevem os embutidos) |
| `settings` | credenciais e preferências em runtime |

## Extensão

- **Novo conversor**: subclasse em `converters/strategies/`, registre em
  `strategies/__init__.py`. Declare `priority` e `can_handle`.
- **Novo formato**: adicione à taxonomia em `library/formats.py`.
- **Novo dispositivo**: perfil embutido em `devices/builtin.py` ou pelo painel.
- **Nova rota**: sub-router em `api/` ou `web/routes/` e inclua no agregador.
- **Novo reparo/migração leve**: `library/repairs.py` (idempotente, roda no boot).

Passo a passo detalhado em [`development.md`](development.md).
