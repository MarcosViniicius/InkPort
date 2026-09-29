# RSS / Atom

O worker de feeds aceita **qualquer tipo de feed** e decide o que fazer com cada
item pelo que ele realmente é — não pelo que o feed promete.

## O que o parser extrai

De cada entrada ficam guardados: `guid`, título, link, anexos, o tipo declarado
e — quando existe — o **artigo completo embutido** (`content:encoded` /
`content` / `description`). Guardar o texto embutido é o que faz o worker
funcionar mesmo quando a página não pode ser baixada.

## Como o worker escolhe a fonte

| Situação | Decisão |
| --- | --- |
| Anexo de livro (EPUB/PDF/MOBI/CBZ/ZIP…) | baixa e importa como livro |
| Anexo de áudio (MP3/M4A…) | **ignora** — podcast, não é leitura (nem baixa o áudio) |
| Anexo só de imagem + link do artigo | usa o **link** (evita importar a imagem destacada como se fosse o livro) |
| Artigo completo embutido (≥ 1000 caracteres) | usa direto, **sem baixar a página** (imune a paywall, bloqueio e JS) |
| Só o link | baixa a página |
| Página “fina” (bloqueio/paywall) | usa o texto do feed |
| Download falha | usa o texto do feed; se não houver, aí sim é erro |
| Nada aproveitável | `skipped` com motivo, não erro |

Cada item termina com um estado claro no painel: `downloaded` (importado),
`skipped` (podcast, sem conteúdo, duplicado) ou `error` (falha real).

## Itens duplicados

Quando o conteúdo de um item já existe na biblioteca (mesmo hash), ele é
marcado como `skipped` com o motivo “conteúdo já existe” e não gera um segundo
livro.

## Quantos itens por rodada

O campo **Itens por execução** (`max_items_per_run`) é quantos itens **novos**
uma execução pode processar — não é um corte no feed. A busca percorre a lista
inteira, pula os que já foram vistos e para depois de processar esse número;
o restante fica registrado no log (“left_for_next_run”) e entra na execução
seguinte. Assim um feed com mais entradas que o limite é consumido por
completo ao longo das rodadas, sem nunca “esquecer” os itens mais antigos.

## O que o feed publica é o que existe para buscar

O servidor importa o que o feed oferece. Muitos sites publicam apenas os N
posts mais recentes no RSS/Atom (por exemplo, Hugo com `rssLimit`, ou os 10–20
itens usuais de um blog) e não expõem paginação. Nesse caso, os posts antigos
**não estão em nenhum lugar do feed** e precisam de outra fonte: o sitemap do
site (veja abaixo). Vale conferir quantas entradas o feed traz antes de supor
que faltou importação.

## Acervo completo do site (sitemap)

Quando o feed não carrega o histórico, importe pelo sitemap. A ferramenta lê o
`sitemap.xml` (inclusive sitemaps-filhos), filtra pelos posts de um ano e passa
cada post pelo mesmo caminho do RSS: baixar → importar → converter.

```powershell
# só relatar (não baixa nada): quantos posts do ano e quantos já temos
python -m app.tools.import_site https://site/sitemap.xml --year 2026 --list

# importar o ano atual (limite por execução opcional)
python -m app.tools.import_site https://site/sitemap.xml --year 2026 --limit 30

# acervo inteiro, com formato/perfil e categoria explícitos
python -m app.tools.import_site https://site/sitemap.xml --all-years `
    --category rss/meu-blog --format epub --profile xteink_x4_pro
```

- Por padrão usa o **ano atual**; `--all-years` pega tudo.
- A **categoria, o formato e o perfil** são herdados do feed cadastrado para o
  mesmo site (o que mantém o blog num lugar só); `--category/--format/--profile`
  sobrescrevem.
- **Não duplica**: URLs já importadas (pelo `source_url` do livro ou pelo
  histórico do feed) são puladas, comparando host sem `www` e sem barra final.
- Posts que falham não interrompem o resto; o resumo final mostra importados,
  enfileirados, já existentes e erros.
- A conversão é feita pelo worker do servidor, então deixe o app rodando: os
  livros aparecem na biblioteca conforme ficam prontos.

## Puxar posts antigos (retroativos)

O RSS só traz os posts recentes que o site publica. Para trazer o histórico de
**um feed específico**, o formulário do feed tem o campo **Buscar do passado**
com presets: *Desligado*, *últimos 6 meses*, *último 1 ano*, *últimos 2 anos*,
*últimos 5 anos* e *todo o acervo do site*. Há também **Sitemap (opcional)**
para quando o sitemap não está em `/sitemap.xml`.

Como funciona:

- Ligado, o worker lê o `sitemap.xml` do site (o mesmo caminho do
  `import_site`) e importa os posts cuja **data na URL** cai dentro do período.
- A data sai da própria URL (`/2026/09/28/slug/`), então funciona em sites com
  esse padrão (Hugo, WordPress, Jekyll…). Sem data na URL não há como datar o
  post e nada é importado — nesse caso use o `import_site`.
- Os posts entram com a **categoria, o formato e o perfil** do feed, e são
  marcados no histórico do feed (aparecem em *Itens recentes*).
- **Não duplica**: URLs já na biblioteca ou já vistas pelo feed são puladas.
- É **automático**: começa ao salvar e continua a cada busca do feed enquanto
  sobrar algo, sempre em blocos de até **Itens por execução** (um acervo grande
  é consumido em várias rodadas, sem travar). O botão **Buscar retroativos**
  força outra passada; quando o período é percorrido por completo, o feed mostra
  `passado: … (concluído)`.
- Se o site não estiver com o sitemap acessível, a busca retroativa tenta de
  novo na próxima rodada (não marca "concluído").
- Mudar o período reinicia a varredura do zero.

Via API, os campos são `backfill_months` (`0` = desligado, `-1` = todo o
acervo) e `sitemap_url`; o `POST /api/feeds/{id}/refresh` já executa a busca
retroativa pendente.

## Editar um feed

Cada feed tem um botão **Editar** (na lista e implícito no nome), que abre
`/feeds/<id>/edit` com o mesmo formulário da criação já preenchido: nome, URL,
frequência, formato de saída, perfil de dispositivo, subcategoria, itens por
execução e as caixas *ativo* / *manter o original*.

O que muda vale para os itens **importados a partir de agora**:

- formato e perfil de dispositivo são lidos no momento em que o item é convertido;
- a subcategoria (ou o nome) define a categoria `rss/<subcategoria ou nome>`; os
  livros já importados são reposicionados na próxima manutenção (o boot roda uma,
  e há o botão em *Configurações → Manutenção*);
- para reaplicar as configurações novas aos itens antigos, use **Refazer feed** —
  ele apaga os livros deste feed e reimporta tudo.

Mudar a URL não apaga nada: o feed passa a apontar para o endereço novo e os
itens já vistos daquela lista continuam marcados (use *Limpar histórico* se
quiser reimportá-los). O endereço é único: tentar cadastrar duas vezes a mesma
URL mostra um aviso e não cria um feed repetido.

Via API, o equivalente é `PATCH /api/feeds/<id>`.

## Regenerar depois de melhorar o conversor

O painel tem o botão **Limpar histórico** em cada feed: ele esquece os itens já
processados para que o próximo *Buscar agora* importe tudo de novo com o
conversor atual. Os livros já convertidos continuam na biblioteca — apague os
antigos se quiser substituí-los.

Via API:

```bash
curl -X POST http://localhost:8080/api/feeds/1/reset
curl -X POST http://localhost:8080/api/feeds/1/refresh
```

## Conversão do artigo

Artigos são convertidos pelo mesmo pipeline do site
[webpagetoepub.github.io](https://webpagetoepub.github.io/) — veja
[`docs/conversion.md`](conversion.md#página-web--epub). Um artigo comum vira
**um capítulo**; os `<h2>` continuam dentro do texto, e só artigos muito longos
(acima de ~25 mil caracteres) são divididos em capítulos por seção.
