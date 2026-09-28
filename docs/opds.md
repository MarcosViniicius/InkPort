# OPDS

Implementação dupla: OPDS 1.2 (Atom) em `/opds` e OPDS 2.0 (JSON) em `/opds/v2`.
Os feeds consultam a biblioteca diretamente; nenhum arquivo é duplicado.

## OPDS 1.2

| Rota | Tipo | Descrição |
| --- | --- | --- |
| `/opds` | **aquisição** | raiz: entrega os livros recentes **e** as seções de navegação (modo configurável, veja abaixo) |
| `/opds/browse` | navegação | catálogo só com as seções, também como `<entry>` (para clientes que só listam entradas) |
| `/opds/new` | aquisição | adicionados recentemente |
| `/opds/all` | aquisição | todos os livros (paginado) |
| `/opds/device/{slug}` | aquisição | **catálogo pronto para um dispositivo** (ex.: `xteink_x4_pro`) |
| `/opds/devices` | navegação | lista um catálogo por dispositivo |
| `/opds/authors` | navegação | índice de autores |
| `/opds/authors/{nome}` | aquisição | livros do autor |
| `/opds/categories` | navegação | índice de categorias |
| `/opds/categories/{slug}` | aquisição | livros da categoria |
| `/opds/series` · `/opds/series/{nome}` | navegação/aquisição | séries |
| `/opds/tags` · `/opds/tags/{nome}` | navegação/aquisição | tags |
| `/opds/search?q=` | aquisição | busca |
| `/opds/search.xml` | OpenSearch | descrição da busca |
| `/opds/books/{id}` | aquisição | entrada de um livro |
| `/opds/cover/{id}` | imagem | capa |
| `/opds/thumbnail/{id}` | imagem | miniatura (gerada e cacheada) |
| `/opds/download/{id}` | arquivo | download direto |

Detalhes relevantes para clientes:

- A **raiz `/opds` é um feed de aquisição**: lista os livros recentes diretamente.
  Isso é de propósito — clientes simples (como o navegador OPDS do firmware
  CrossPoint no Xteink) só renderizam entradas com link de download e mostrariam
  "nenhuma entrada" se a raiz fosse apenas de navegação.
- As seções do catálogo (Autores, Categorias, Séries, Tags) aparecem na raiz como
  links **no nível do feed** (`rel="subsection"`) **e** como `<entry>`, para que
  clientes de celular que só desenham entradas não mostrem uma lista vazia.
- `Content-Type` de navegação: `application/atom+xml;profile=opds-catalog;kind=navigation`.
- `Content-Type` de aquisição: `...;kind=acquisition`.
- Links de aquisição usam a relação `http://opds-spec.org/acquisition`, com
  `type` e `length`.
- **Livro sem autor não publica `<author>`**: o feed declara um autor no nível do
  feed (exigência do Atom) e a entrada fica sem autor, então clientes que montam
  o nome do arquivo a partir de título + autor usam o **título** — antes eles
  salvavam tudo como “Autores desconhecidos”. O nome sugerido no
  `Content-Disposition` do download é sempre `<título>.<formato>`.
- Capa e miniatura usam `http://opds-spec.org/image` e `.../image/thumbnail`.
- Paginação via OpenSearch (`totalResults`, `startIndex`, `itemsPerPage`) com
  `rel="next"` e `rel="previous"`.

### O que a raiz mostra (`OPDS_ROOT_MODE`)

Alguns leitores preferem que a raiz seja só o "índice" do catálogo, sem livros
misturados. Como o CrossPoint (Xteink) precisa de livros na raiz, isso é uma
escolha sua:

| Modo | O que `/opds` entrega | Quando usar |
| --- | --- | --- |
| `mixed` (padrão) | livros recentes **+** as seções | funciona em todo cliente, inclusive CrossPoint |
| `navigation` | só as seções (sem livros, sem paginação) | leitores Android que listam entradas e querem só navegar |
| `books` | só os livros (as seções ficam em `/opds/browse`) | quando a raiz deve ser a estante |

```dotenv
OPDS_ROOT_MODE=navigation   # no .env, seguido de reinício
```

**Leitores simples (CrossPoint/Xteink)**: eles só listam entradas com link de
download, então aponte o catálogo para **`/opds/device/{slug}`** (ex.:
`/opds/device/xteink_x4_pro`) ou para `/opds/all` — assim a raiz pode ficar só
com os menus e o aparelho continua vendo os livros.

**Sem trocar o modo**, o caminho mais simples para um leitor de celular é
apontar o catálogo dele para `http://SEU-ENDERECO/opds/browse`: ali só existem as
seções (Novidades, Todos os livros, Por dispositivo, Autor, Categoria, Série,
Tag), como `<entry>`, e a raiz continua servindo o Xteink.

### Variantes de um mesmo título

Um livro pode ter um original e várias conversões (EPUB do Xteink, MOBI do
Kindle...). A entrada do OPDS lista **todos os arquivos relacionados** como
links de aquisição, ordenados por prioridade de formato, para que o cliente
escolha o que consegue abrir.

As listagens **colapsam variantes**: se o original ainda existe, a conversão não
gera uma entrada extra — ela aparece como link de download do mesmo título. Se o
original foi removido, a conversão passa a ser a entrada principal.

Cada variante vira um link de aquisição com um título descritivo
("Baixar EPUB — Xteink X4 Pro"), ordenados com o **universal primeiro**, porque
clientes simples (como o CrossPoint) usam o primeiro link utilizável.

### Catálogo por dispositivo

`/opds/device/xteink_x4_pro` entrega, para cada obra, o arquivo **feito para
aquele aparelho** primeiro (e a versão universal como alternativa). É a forma de
apontar um leitor específico sem depender de detecção de dispositivo — o
CrossPoint, por exemplo, não envia `User-Agent`.

## Compatibilidade com clientes (CrossPoint / Xteink)

O navegador OPDS do firmware **CrossPoint** (usado no Xteink X4) é restrito; o
nosso feed é gerado para satisfazê-lo:

- Ele só lista uma entrada se ela tiver `<title>` **e** um link utilizável.
- Para um livro, o link precisa de `rel` contendo `opds-spec.org/acquisition`
  **e `type` exatamente `application/epub+zip`**. Como EPUB é um container ZIP,
  anunciar `application/zip` faz **todos os livros desaparecerem** da tela — por
  isso o MIME é sempre normalizado pelo formato (ver `library/formats.py`).
- Para navegação, o link precisa de `type` contendo `application/atom+xml`.
  Por isso a raiz traz as seções como entradas navegáveis.
- O nome do arquivo salvo no aparelho vem de `<title>` + `<author><name>` e
  sempre termina em `.epub`.

O teste `tests/crosspoint_compat.py` reproduz essas regras com o mesmo parser do
firmware (expat) e falha se algum link deixar de ser reconhecido.

## OPDS 2.0

| Rota | Descrição |
| --- | --- |
| `/opds/v2` | navegação (JSON) |
| `/opds/v2/new`, `/opds/v2/all` | publicações |
| `/opds/v2/search?q=` | busca |
| `/opds/v2/authors`, `/opds/v2/categories` | coleções |
| `/opds/v2/books/{id}` | publicação |

`Content-Type`: `application/opds+json`.

## Endereço nos links

Os links do feed (download, capa, busca, paginação) usam **o endereço pelo qual
o cliente chegou**, lido do cabeçalho `Host` — não um IP adivinhado pelo
servidor. Isso é o que faz funcionar por Tailscale, VPN, IP da LAN, hostname ou
domínio sem reconfigurar nada.

Para fixar um endereço canônico (proxy reverso, domínio público), defina
`BASE_URL` e `USE_REQUEST_HOST=false`.

## Autenticação

Se `OPDS_USERNAME` e `OPDS_PASSWORD` estiverem definidos no `.env`, todas as
rotas OPDS exigem **HTTP Basic**. Muitos leitores aceitam credenciais embutidas
na URL do catálogo; outros têm um campo de usuário/senha.

```
http://usuario:senha@SEU-IP:8080/opds
```

## Testar sem um e-reader

```bash
curl -H "Accept: application/atom+xml" http://localhost:8080/opds
curl http://localhost:8080/opds/v2/
curl -u user:pass http://localhost:8080/opds/all
```
