# Conversão

A camada de conversão é modular e orientada a estratégia. Cada conversor
declara o que sabe fazer; o planejador escolhe o destino; o registro escolhe o
conversor; o runner executa.

## Pré-requisitos por recurso

| Recurso | Precisa de |
| --- | --- |
| Imagens → EPUB/CBZ/PDF | nada (Pillow) |
| Compressão/redimensionamento | nada (Pillow) |
| EPUB → EPUB otimizado | nada (zipfile + Pillow) |
| CBZ/ZIP, CBT/TAR → qualquer | nada (zipfile / tarfile) |
| CB7/7z → qualquer | `py7zr` (pacote Python) |
| CBR/RAR → qualquer | **nada** — usa a `unrar.dll` que acompanha o projeto (arquivos *solid* e multi-volume inclusive); `rarfile` é o plano B |
| PDF → imagens/CBZ/EPUB | PyMuPDF (pacote Python) — sem poppler/Ghostscript |
| PDF com texto → EPUB reflow | PyMuPDF (reflow nativo) |
| EPUB → MOBI/AZW3 | escritor KF8/MOBI nativo (Python) |
| EPUB → KEPUB | zips + spans do Kobo (Python) |
| EPUB → PDF | PyMuPDF `Story` (Python) |
| EPUB → DOCX / FB2 / TXT | `python-docx` / XML / texto (Python) |
| TXT/RTF/FB2/DOCX/MOBI → EPUB | texto, `lxml`, `python-docx`, `striprtf`, `mobi` (Python) |

Nenhum programa externo é necessário: tudo acima vem em `requirements.txt`, mais
a biblioteca UnRAR redistribuída em `app/vendor/unrar` (licença em
`app/vendor/unrar/LICENSE-UnRAR.txt`). Se a máquina já tiver Calibre, ele continua
disponível como último recurso para formatos exóticos (`.lit`, `.odt`, `.doc`).

## Estratégia automática

`converters/planner.py` decide assim:

1. **Imagem única** → JPG redimensionado para o perfil.
2. **Quadrinho (CBZ/CBR) ou PDF de imagens** → pipeline de páginas:
   - perfil com `comic_output = "epub_images"` (Xteink, Kindle) → **EPUB de imagens**;
   - perfil com `comic_output = "cbz"` (Kobo) → CBZ;
   - perfil com `comic_output = "pdf"` (tablet) → PDF.
3. **PDF com texto** → **reflow nativo** (PyMuPDF): títulos por tamanho/peso,
   de-hifenização, remoção de cabeçalho/rodapé, leitura coluna a coluna.
4. **E-book** → formato preferido do perfil; EPUB→EPUB vira **otimização**.

Em todos os casos, as imagens passam por `converters/normalise.py`:

```
abrir → (EXIF) → tons de cinza → autocontraste → recortar margens
      → redimensionar (Lanczos, fit ou universal) → realce → contraste/gamma
      → limite de segurança 2048x3072 → salvar (JPG/PNG)
```

## Pipeline de imagens em detalhe

Desenhado para qualidade (referência: o motor do KCC usado no mangaconverter.com),
não apenas para "reduzir":

- **Coleta** (`collectors.py`): ordena as páginas em ordem natural
  (`page2` antes de `page10`).
- **PDF — extração antes de render** (`pdf_render.py`): se a página é um único
  scan embutido cobrindo a página, a **imagem original é extraída** — sem
  re-render e sem borrão. Só quando não há imagem embutida a página é renderizada.
- **DPI calculado** (`_auto_dpi`): em vez de um DPI fixo baixo, o DPI vem do alvo
  (com supersampling de 1,6×), para que o downscale seguinte preserve detalhe.
  Renderização direta em tons de cinza quando o perfil é P&B.
- **Redimensionamento**:
  - modo *dispositivo* → `fit_image` com Lanczos, sem upscale por padrão;
  - modo *universal* → mantém a resolução, aplicando apenas o teto
    (`max_long_side`).
- **Limite de segurança**: nada acima de **2048 × 3072** — acima disso o firmware
  do Xteink recusa a imagem.
- **Níveis**: `autocontrast` opcional (recupera digitalizações escuras/claras).
- **Recorte de margens** com limite de segurança (não corta conteúdo).
- **Tons de cinza** obrigatório em e-ink; dither Floyd–Steinberg quando o perfil
  tem 2 níveis.
- **Realce** (unsharp, `sharpen`) aplicado **depois** do resize — antes só
  amplificaria o ruído.
- **Formato de saída**: JPG (menor) ou **PNG** (sem perda, melhor para traço).

### Texto de mangá/quadrinhos (experimental e modos precisos)

Para telas pequenas, o texto de balões costuma ficar minúsculo mesmo com espaço
sobrando. O tratamento é escolhido por conversão (e por valor padrão na tela de
Configurações): desligado, **experimental** (só geometria, sem OCR) ou os dois
**modos precisos** com OCR. No modo experimental, um motor local
(`app/converters/manga/`, só Pillow) faz, por página:

1. **Detecção** (`detector.py`): binariza, faz *flood fill* a partir da borda
   para achar as áreas brancas **fechadas** (balões e caixas de texto) e rotula
   os componentes. Dentro de cada região, os traços do texto são os blobs de
   tinta que **não tocam a borda** (o contorno do balão toca).
2. **Análise de espaço** (`layout.usable_box`): varre o espaço livre linha a
   linha e devolve a maior **caixa útil** dentro da região — num balão redondo
   isso é o retângulo inscrito, então o texto não invade o contorno.
3. **Ampliação** (`enlarge.py`): recorta o texto como imagem, limpa o original
   com a cor de fundo do balão e cola de volta maior (Lanczos). A estratégia B
   permite passar um pouco do balão, mas só sobre **espaço livre** — se houver
   arte ali, a escala é reduzida.
4. **Reflow por palavras** (`segmentation.py` + `reflow.py`): quando a quebra de
   linha original limita o crescimento, o bloco é dividido em **palavras** (por
   projeção da tinta, sem OCR) e re-empacotado com a maior escala que couber na
   caixa útil — mais linhas, quebras diferentes, espaçamento compacto. É o que
   destrava os balões "cheios". A ordem de leitura é respeitada: mangá é
   right-to-left, então a primeira palavra da linha fica à direita.

É **best-effort**: página que não pode ser interpretada volta sem alteração e
nunca quebra a conversão. Sem IA e sem binário externo.

#### Modos precisos (OCR opcional)

A geometria pura erra em páginas densas (palavras quebradas, ordem trocada).
Para isso existem dois modos que usam **OCR opcional**:

- **`ocr`** — o OCR lê a página, então as palavras e a ordem de leitura são
  conhecidas; os **glifos originais** continuam sendo os mesmos (sem risco de
  texto errado), mas são recolocados e ampliados com precisão.
- **`ocr_font`** — o mesmo OCR e o texto é **reescrito** com a fonte embutida
  (Comic Neue, OFL) no maior tamanho que couber no balão. É o mais nítido; por
  segurança, regiões com confiança abaixo de `manga_font_min_score` caem no
  modo conservador.

Backend: **RapidOCR** (PP-OCRv6 multilíngue, ONNX Runtime, CPU) — Apache-2.0,
modelos *mobile* de 5 a 35 MB. É uma dependência **opcional**
(`pip install .[ocr]`), como o Calibre: sem ela, os modos precisos caem para o
experimental. Os modelos baixam uma única vez para `DATA_DIR/ocr/models` e
depois funcionam offline. No acervo de teste (scanlation em português,
480 px) o modelo *small* lê a página com 0,93–1,00 de confiança em ~0,7 s.

A opção aparece nas **Opções avançadas** da conversão e também na **importação**
(quando «Converter automaticamente» está ligado), então dá para importar e já
sair com o texto tratado sem passar pela página do livro.

Limites configuráveis em **Configurações → Conversão e armazenamento →
«Avançado»**: `manga_max_scale` (teto, padrão 2×), `manga_overflow` (quanto pode
passar do balão, padrão 0,15) e `manga_max_overflow_px` (teto em pixels).
A saída vira um novo `Book` ligado ao original por `origin_book_id`, então o
painel/OPDS já mostram **original × adaptado** lado a lado.

## Páginas duplas (spreads)

Um volume de mangá tem páginas normais (retrato) e **páginas duplas desenhadas**
(spreads, paisagem). Num leitor de tela retrato, uma spread reduzida vira uma
tira pequena — o KCC resolve isso em `image.py::splitCheck`, e o conversor segue
a mesma regra:

| Proporção (largura/altura) | Ação |
| --- | --- |
| ≤ 1,16 | nada (não é spread) |
| 1,16 a 1,8 | **divide em duas páginas** |
| ≥ 1,8 | **gira 90°** (preenche a tela quando o aparelho é virado) |

- A ordem das metades respeita a **direção de leitura**: em mangá (RTL) a metade
  **direita** vem primeiro, como manda o `--manga-style` do KCC.
- Configurável por perfil (`spreads`): `auto` (como o KCC), `split`, `rotate`,
  `both` (divide e gira) ou `none` (não mexer).
- Só vale para perfis **com tela definida**; nos perfis universais o padrão é
  `none`, porque quem decide é o leitor.

Referência: KCC `--splitter` (`0: Split` é o padrão, `1: Rotate`, `2: Both`).

## Página web -> EPUB

Três caminhos usam este mesmo pipeline: um arquivo **HTML** importado no painel,
uma **página baixada pela URL** (Importar → *Baixar de uma URL*) e cada **item de
um feed RSS/Atom**. No caso da URL, o endereço fica guardado no livro
(`source_url`, visível em *Origem*) — é isso que faz as imagens e os links
relativos resolverem.

A conversão de páginas é um **port do
[html2epub](https://github.com/webpagetoepub/html2epub)** (MIT), a biblioteca do
site [webpagetoepub.github.io](https://webpagetoepub.github.io/). O pipeline
segue a mesma ordem de etapas:

1. `prepare_lazy_images` — promove `data-src`/`<source srcset>` para `src`
   (acréscimo nosso: a referência limpa os `data-*` cedo e perde imagens com
   *lazy loading*).
2. `clean_document` — remove elementos (vídeo, áudio, iframe, canvas, formulários,
   script, style, nav, math, svg…), elementos ocultos, comentários, elementos
   vazios, atributos `data-*`, atributos de estilo/evento/tamanho, espaços
   repetidos e SVGs vazios.
3. `get_main_content` — escolhe `main article` → `main`/`role=main` → `article`
   → `body`. **Diferença deliberada:** a referência troca `main`/`article` por
   `div` *antes* de escolher, o que faz a escolha cair sempre no `<body>` inteiro
   (e o cabeçalho/rodapé do site entrar no livro). Aqui a escolha vem antes.
4. `replace_elements` — reduz níveis de título, troca tags simples por
   `div`/`span`/`p`/`ul`/`del`, converte tags desconhecidas em `div` e aplica CSS
   a `mark`, `u`, `center`, `table`, `th`, `td`.
5. `load_images` — resolve as URLs, baixa (uma vez por URL) e embute no EPUB;
   imagem que falha é descartada.
6. `split_main_content` — divide o texto por `<h2>` (ou `h2`/`h3` quando há um
   só `h2`) e transforma o texto restante (> 80 caracteres) em um capítulo final.
7. `fix_links` — converte links para absolutos, âncoras locais viram `#âncora`,
   links externos ganham `target="_blank"` e âncoras quebradas viram texto.

### Capítulos pensados para o leitor

A referência cria **um capítulo por `<h2>`** — num artigo comum isso vira 8 ou 9
páginas minúsculas e um índice inútil no e-reader. Aqui há uma etapa extra,
`consolidate_chapters`:

- artigo com menos de ~25 000 caracteres de texto → **um único capítulo** (os
  `<h2>` continuam no texto, a estrutura não se perde);
- artigo maior → mantém seções com pelo menos ~1 500 caracteres e absorve as
  menores no capítulo anterior.

## Reduzir tamanho sem perder conteúdo

Números medidos num PDF de mangá real (One Piece 1141, 15 páginas, scan em
halftone), convertido para o Xteink X4 Pro:

| Configuração | Tamanho | Por página |
| --- | --- | --- |
| PDF de origem (scan) | 41,25 MB | — |
| EPUB universal (`manga_epub`, 1600×2400, cor, JPEG) | 13,61 MB | — |
| EPUB Xteink, primeira tentativa (800×480, JPEG q86) | 2,08 MB | 137 KB |
| **EPUB Xteink atual (480×800, PNG 16 níveis)** | **1,24 MB** | **81 KB** |

Do original para o atual: **41,25 MB → 1,24 MB (97% menor)**, sem perder
conteúdo no aparelho.

O ganho vem de três decisões, nesta ordem de impacto:

1. **Resolução correta** (480 × 800 retrato). É o maior fator: páginas na tela
   do aparelho, sem reescalonamento. Só isso levou 41 MB → ~2 MB.
2. **Quantizar para os níveis reais do painel** (`posterize_levels = 16`) e
   guardar em **PNG sem perda**. O painel e-ink exibe exatamente 16 níveis de
   cinza, então 16 níveis não é perda — é a capacidade dele. Medido: 2,00 MB
   (JPEG q86) → 1,26 MB (PNG 16 níveis), **37% menor e sem artefatos de
   compressão**. O JPEG gastaria bits com o retículo/halftone que o painel nem
   consegue mostrar.
3. **Não recomprimir o que já está comprimido**: imagens entram no ZIP com
   `ZIP_STORED` (o deflate não ganha nada em PNG/JPEG).

O que foi testado e **não** ajudou (medições, não suposição):

| Tentativa | Resultado |
| --- | --- |
| Posterizar em 16 níveis mantendo JPEG | 2,00 → 1,79 MB (11%) — pouco |
| Filtro de ruído (mediana 3) | PSNR 14 dB — **destrói** o retículo |
| Filtro gaussiano 0,5 | 1,37 MB, e suaviza o traço |
| WebP q80 | 1,41 MB, mas o firmware só decodifica JPEG/PNG |

Regra prática: **se o aparelho é e-ink, use 16 níveis + PNG**. Se o arquivo é
para distribuição geral (universal), mantenha 256 níveis e JPEG de qualidade
alta — aí a resolução precisa ser preservada.

## Universal x por dispositivo

Um dos propósitos do EPUB é adaptar-se a qualquer tela. Por isso há dois modos:

- **Universal** (`generic_epub`, `manga_epub`): não fixa a tela; mantém a
  resolução da origem (dentro do teto) e cada leitor ajusta a página ao seu
  visor. É o padrão e o recomendado quando não se sabe o aparelho.
- **Por dispositivo** (`xteink_x4_pro`, `kindle_*`, `kobo_*`): já dimensiona para
  o painel. Fica mais nítido naquele aparelho (o downscale do firmware é
  *nearest-neighbour*) e o arquivo fica menor.

No OPDS há uma rota por dispositivo (`/opds/device/<slug>`) que entrega, para
cada obra, o arquivo feito para aquele aparelho — com a versão universal como
alternativa.

## EPUB gerado

O escritor próprio (`converters/epub/`) monta um EPUB 3 válido com fallback
EPUB 2 (`toc.ncx`), capa, metadados Dublin Core, série (Calibre) e
`page-progression-direction="rtl"` para mangá. O `mimetype` é gravado primeiro e
sem compressão, como exige a especificação.

## Catálogo de saídas

`converters/catalog.py` monta, para cada arquivo e perfil, a lista de destinos
compatíveis com rótulo, conversor e observação — é isso que o painel mostra ao
importar um arquivo.

## Adicionar um conversor

```python
from app.converters.base import BaseConverter, ConversionRequest, ConversionResult

class MeuConversor(BaseConverter):
    name = "meu_conversor"
    priority = 45                      # maior vence empatado

    def can_handle(self, request: ConversionRequest) -> bool:
        return request.target_format == "xyz"

    def run(self, request: ConversionRequest) -> ConversionResult:
        request.report(50, "processando")
        out = request.workdir / "output.xyz"
        ...
        return ConversionResult(output_path=out, converter=self.name)
```

Registre em `converters/strategies/__init__.py` (ou no registro, se for um
backend externo).
