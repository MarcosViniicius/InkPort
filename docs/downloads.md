# Rastreamento de downloads

O servidor registra quando um arquivo é **efetivamente entregue** por um cliente
— no fluxo de bytes, não no acesso ao feed. A ideia central: uma entrada de feed
ou um clique num link não é download; download é a resposta de arquivo que
terminou de ser enviada.

## Onde o rastreamento acontece

Os arquivos são servidos por dois endpoints (as URLs públicas **não mudaram**):

| Endpoint | Quem usa |
| --- | --- |
| `GET /opds/download/{book_id}` | OPDS 1.2 **e** 2.0 (links de aquisição) |
| `GET /library/{book_id}/file` | painel, botão "Baixar" e Web Reader |

O `/reader/{id}/raw` (capítulo/página dentro do leitor, `inline`) é
**visualização**: não conta como download e fica de fora de propósito.

Ambos entregam o arquivo por uma `TrackedFileResponse` (`app/downloads/response.py`),
que é uma `FileResponse` normal (Range, ETag, streaming) com o `send` do ASGI
envolto para contar os bytes. Nada é carregado em memória: o arquivo continua
sendo lido em blocos de 64 KB pelo próprio Starlette.

## Como funciona

1. A rota resolve o arquivo e chama `store.start_download(...)`: cria (ou
   atualiza) o registro do arquivo, abre um **evento** com estado `started` e
   incrementa `active_downloads` (com `UPDATE ... = active_downloads + 1`, à
   prova de concorrência). O cliente é identificado pelo `User-Agent`.
2. A resposta entrega os bytes e conta o que passa pelo `send`.
3. Ao terminar, `store.finish_download(...)` fecha o evento e atualiza o
   agregado: `active_downloads - 1`; se concluiu, `download_count + 1`,
   `last_download_at`/`first_download_at` e o estado vira `downloaded`.

**Fonte da verdade da conclusão:** a resposta terminou sem exceção. Se o cliente
(ou o proxy) cai no meio, a tarefa é cancelada (`asyncio.CancelledError`) e o
evento é fechado como `interrupted`; uma exceção de servidor vira `error`. Quando
o servidor usa `http.response.pathsend` (o corpo sai direto do arquivo, sem
passar pelo `send`), o total é usado como bytes enviados no fim bem-sucedido.

## Onde os dados ficam

No **mesmo banco cifrado** da aplicação (`DATA_DIR/opds.db`, SQLCipher), em duas
tabelas:

**`file_records`** — um por arquivo (por livro); é a entidade que a futura
gerência de arquivos manipula:

| Campo | Para quê |
| --- | --- |
| `id` | identificador interno do arquivo |
| `book_id` | FK para `books` (fica `NULL` se o livro for apagado — o histórico permanece) |
| `file_hash` | SHA-256 do arquivo (identidade estável, sobrevive a renomeação) |
| `file_name`, `file_ext`, `size_bytes` | nome/extensão/tamanho servidos |
| `state` | `available` \| `downloaded` \| `blocked` \| `deleted` |
| `download_count` | downloads concluídos |
| `active_downloads` | quantos em andamento agora |
| `first_download_at`, `last_download_at` | primeiro/último download |
| `created_at`, `updated_at` | bookkeeping |

**`download_events`** — um por tentativa:

| Campo | Para quê |
| --- | --- |
| `file_id`, `book_id` | a que arquivo pertence |
| `kind` | `download` (reservado para `view` no futuro) |
| `status` | `started` \| `completed` \| `interrupted` \| `error` |
| `client`, `client_key` | rótulo amigável (ex.: `KOReader`) + hash curto do UA |
| `range_request`, `resume` | se veio com `Range` e se é retomada |
| `bytes_sent`, `bytes_total` | quantos bytes saíram / tamanho total |
| `started_at`, `finished_at`, `error` | quando começou/terminou e o motivo |

Privacidade: **não** guardamos IP nem o `User-Agent` cru. Só o rótulo de família
(ex.: `KOReader`, `Navegador`) e um hash de 12 caracteres do UA, que distingue
dois aparelhos sem ser reversível.

## Estados

**Arquivo** (`file_records.state`):

| Estado | Significado |
| --- | --- |
| `available` | disponível, ainda não baixado |
| `downloaded` | já foi baixado (continua sendo oferecido) — "baixado anteriormente" |
| `blocked` | "não baixar novamente"; deve deixar de ser oferecido no RSS/OPDS |
| `deleted` | removido do armazenamento |

**"Download iniciado"** não é um estado persistido (para não ficar preso se o
processo morrer): é derivado de `active_downloads > 0` ou de um evento `started`.

**Tentativa** (`download_events.status`): `started` → `completed` /
`interrupted` / `error`.

## Bloqueio no catálogo (já ligado)

Arquivos em `blocked`/`deleted` **deixam de aparecer** nos catálogos:

- as listagens do OPDS 1.2 e 2.0 filtram por estado (a contagem da paginação
  acompanha, porque o filtro entra na consulta);
- a entrada individual (`/opds/books/{id}`, `/opds/v2/books/{id}`) responde 404;
- o download (`/opds/download/{id}`) responde 404;
- um arquivo relacionado bloqueado não vira link de aquisição.

O **painel continua mostrando** o livro (com a situação), para o dono decidir —
o filtro é só nas consultas do OPDS (`opds/queries.py` → `list_books`), não nas
do painel. No detalhe do livro há os botões **Bloquear novo download** /
**Liberar no catálogo** e **Marcar como baixado**.

## Downloads grandes, Range e retomada

- O arquivo é transmitido em blocos; nada é lido inteiro para memória.
- Range e retomada são do próprio `FileResponse`; só observamos o que ele envia.
- Um `Range` **fechado** (`bytes=0-99`, um pedaço) **não** conta como download.
  Um `Range` **aberto** (`bytes=100-`, do ponto até o fim) conta, porque
  completa o que faltava. Vários ranges (multipart) também não contam.
- Limite por `HEAD`/`304`: não há corpo, então não entra no contador.

## Interrupções e concorrência

- Vários downloads do mesmo arquivo ao mesmo tempo: cada um tem o seu evento, e
  os contadores usam expressões SQL (`col + 1` / `max(0, col - 1)`) para não
  perder atualização.
- No boot, `recover_interrupted(..., older_than_seconds=0)` fecha como
  `interrupted` tudo que ficou `started` (processo caiu no meio) e zera
  `active_downloads`. A manutenção periódica repete para eventos pendurados há
  mais de 6 h.

## Limpeza automática

Configurável em **Configurações → Limpeza automática** (fica no banco, como o
resto da configuração):

| Ajuste | Padrão | O que faz |
| --- | --- | --- |
| Limpeza automática de arquivos | desligada | liga a rotina |
| Remover depois de (dias) | 30 | prazo desde o último download (ou desde o bloqueio) |
| Incluir arquivos originais | desligado | desligado, só remove arquivos gerados por conversão |

Elegível = estado `downloaded` ou `blocked`, **sem download em andamento** e
ocioso há mais que o prazo. Rodar com os originais desligados é o padrão seguro:
a limpeza mira os artefatos de conversão que o usuário já baixou, não os
originais que ele importou.

Ao remover: o arquivo sai do disco, o livro sai da biblioteca e o
**registro vira `deleted`** (com `book_id` nulo) — o histórico permanece. A
rotina roda na manutenção periódica (a cada ~15 min, quando ligada) e há um
**Limpar agora** em *Configurações → Manutenção*, que mostra antes quantos
arquivos seriam removidos e quanto espaço isso libera.

## API (base para a gerência futura)

| Rota | Efeito |
| --- | --- |
| `GET /api/downloads?state=&limit=` | lista os arquivos rastreados |
| `GET /api/downloads/book/{book_id}` | o registro de um livro |
| `POST /api/downloads/book/{book_id}/downloaded` | marca como baixado |
| `POST /api/downloads/book/{book_id}/block` | marca "não baixar novamente" |
| `POST /api/downloads/book/{book_id}/unblock` | desfaz o bloqueio |

## Como estender depois

A camada de domínio (`app/downloads/store.py`) já expõe o que as metas futuras
precisam; falta só ligar nas telas/consultas:

1. **Marcar como baixado** — `store.mark_downloaded(session, book_id)`.
2. **Não baixar novamente / bloquear** — `store.block(...)` / `store.unblock(...)`.
3. **Impedir de reaparecer no OPDS** — ✅ já ligado: `opds/queries.list_books`
   passa `exclude_states=store.HIDDEN_STATES` para `repository.search`, e as
   entradas/downloads individuais consultam `queries.is_offerable`. Para incluir
   outros catálogos, use o mesmo `exclude_states` (ou `store.should_offer`).
4. **Excluir do armazenamento** — marque `deleted` (`store.set_state`) e apague o
   arquivo; o histórico fica (o `book_id` pode virar `NULL`).
5. **Evitar reconversão desnecessária** — consulte o registro/estado antes de
   enfileirar em `app/library/conversions.py` (um arquivo `downloaded`/`blocked`
   normalmente não precisa ser gerado de novo).
6. **Histórico mínimo** — `file_records` + `download_events`;
   `store.prune_events(keep_days=...)` poda eventos antigos sem perder o
   agregado.
7. **Limpeza/expiração automática** — ✅ já ligado: `app/downloads/cleanup.py`
   (`CleanupRule`, `plan`, `run`) usa `store.cleanup_candidates` e roda na
   manutenção quando habilitado. Novas regras (tamanho, categoria, só originais)
   entram como campos de `CleanupRule` + `FIELDS`.

## Arquivos

Criados:

- `app/database/models/downloads.py` — `FileRecord`, `DownloadEvent`.
- `app/downloads/__init__.py`, `app/downloads/store.py`,
  `app/downloads/response.py` — domínio + resposta instrumentada.
- `app/api/downloads.py` — API de leitura e estados.
- `app/downloads/cleanup.py` — limpeza automática configurável.
- `tests/downloads.py`, `docs/downloads.md`.

Alterados:

- `app/database/models/enums.py` (`DownloadStatus`, `FileState`),
  `app/database/models/__init__.py` (registro).
- `app/opds/v1/assets.py`, `app/opds/queries.py`, `app/opds/v2.py`,
  `app/library/repository.py` — instrumentação da entrega e filtro de bloqueio.
- `app/web/routes/library.py` — instrumentação da entrega, botão de bloqueio e
  exibição no detalhe.
- `app/workers/maintenance.py` — recuperação de downloads interrompidos e a
  rotina de limpeza.
- `app/security/runtime.py` (`FIELDS`/`GROUPS`), `app/web/routes/settings_routes.py`
  e `app/web/templates/settings.html` — seção «Limpeza automática» e «Limpar agora».
- `app/web/routes/library.py` (detalhe do livro), `app/web/labels.py`,
  `app/web/templating.py`, `app/web/templates/book_detail.html` — exibição.
- `app/api/__init__.py` — registra o router.
- `app/library/repairs.py` — um `.epub` corrompido não derruba mais o boot.
- `docs/development.md`, `AGENTS.md` — mapa e testes.

## Testes

`python tests/downloads.py` cobre: contagem por entrega (OPDS e painel), Range
fechado x aberto, downloads simultâneos, recuperação de um download morto,
estados e candidatos à limpeza.
