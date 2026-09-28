# Dispositivos e formatos

O projeto é feito para **leitores e-ink em geral**. Cada dispositivo é um
**perfil** que declara:

- a tela (largura, altura, PPI, cor ou tons de cinza, níveis de cinza);
- o que ele abre **nativamente** (EPUB, KEPUB, AZW3, CBZ, PDF…), que define o
  formato de saída preferido;
- o pipeline de imagem (qualidade, quantização, recorte, realce, spreads).

Perfis embutidos ficam em `app/devices/builtin.py` e podem ser criados/ajustados
pelo painel (tabela `device_profiles`). Regra do projeto: **um perfil só declara
como nativo o que foi verificado** — comportamento de firmware muda, então
documente a fonte quando adicionar um aparelho.

O perfil padrão é o **E-ink genérico 6"**; há também um **EPUB universal**
(independente de tela) para quando você não sabe onde vai ler.

## Xteink X4 Pro — caso documentado a fundo

Abaixo está a análise verificada do Xteink X4 Pro, que serve de exemplo de como
documentar um aparelho antes de escrever o perfil.

### Hardware

| Item | Valor |
| --- | --- |
| Tela | E Ink 4,3" |
| Resolução do painel | **800 × 480 px** (o buffer físico é gravado na horizontal) |
| **Área de leitura** | **480 × 800 px (retrato)** — é assim que o leitor apresenta as páginas |
| Escala de cinza | 16 níveis (sem cor) |
| Armazenamento | cartão microSD de 16 GB, expansível até 256 GB |
| Conexão | Wi-Fi 2,4 GHz, Bluetooth, porta Pogo Pin |

> A distinção importa: o *painel* é 800 × 480, mas o firmware apresenta a leitura
> em **retrato 480 × 800**. No CrossPoint isso está em
> `src/activities/reader/ReaderActivity.cpp` (`setOrientation(Orientation::Portrait)`)
> e no buffer `lib/Xtc/XtcTypes.h` (`DISPLAY_WIDTH = 480`, `DISPLAY_HEIGHT = 800`).
> Gerar páginas em 800 × 480 faz o leitor reduzi-las a 480 de largura — foi
> exatamente o defeito corrigido no perfil `xteink_x4_pro`.

### Limites do firmware (verificados)

| Limite | Valor | Fonte no firmware |
| --- | --- | --- |
| Largura máxima de imagem | **2048 px** | `JpegToBmpConverter.cpp` / `PngToBmpConverter.cpp` / `Bitmap.cpp` |
| Altura máxima de imagem | **3072 px** | idem |
| Escalonamento na leitura | `scale = min(scaleX, scaleY)` | `ChapterHtmlSlimParser.cpp` |
| Qualidade do downscale do aparelho | *nearest-neighbour* em redução | `JpegToFramebufferConverter.cpp` |

Imagens maiores que 2048 × 3072 são **rejeitadas** ("Image too large or invalid"),
e o downscale interno do aparelho é simples — por isso o servidor nunca emite
acima desses limites e, no modo dispositivo, **pré-reduz com Lanczos**.


## Formatos

| Categoria | Suporte |
| --- | --- |
| Documentos | **EPUB, TXT** |
| Imagens | **JPG, BMP** |
| Fontes | BIN, XTF (TTF "planejado") |
| PDF / MOBI / PNG | **citados em material de marketing, mas a ficha técnica detalhada e a FAQ dizem explicitamente para não assumir compatibilidade** |

> Fonte: FAQ oficial "X4 Pro FAQ: Formats, Storage, Charging, Front Light, and Support"
> ("If your library depends on PDF, MOBI, PNG, a store-specific format, or
> DRM-protected books, confirm compatibility with XTEINK support before ordering.
> Do not assume that every file commonly described as an 'eBook' will open
> directly.")

### Consequências para o projeto

1. **EPUB é o formato de destino garantido.** O perfil `xteink_x4_pro` define
   `preferred_format = "epub"` e só lista `epub`/`txt`/`jpg`/`bmp` como nativos.
2. **CBZ/CBR não são nativos.** Quadrinhos e mangás são entregues como
   **EPUB baseado em imagens** (`comic_output = "epub_images"`), com uma página
   XHTML por imagem. É o caminho que a comunidade usa para ler mangá no X4.
3. **PDF não é o destino preferido** para esse aparelho: só aparece como opção
   secundária, marcado como "preserva o layout, mas não é nativo".
4. **Sem cor.** O perfil força `grayscale = True` e o pipeline converte as
   páginas para tons de cinza (16 níveis).
5. **Sem rotação.** A tela de leitura é retrato (480 × 800), igual às páginas de
   mangá; girar 90° as deixaria deitadas e reduzidas (`rotate_portrait = False`).
6. **Dimensionamento exato.** No perfil do aparelho o arquivo sai em 480 × 800,
   com downscale Lanczos. Isso evita o reescalonamento *nearest-neighbour* do
   firmware e mantém os traços nítidos.
7. **Tetos de segurança.** Nada é emitido acima de **2048 × 3072**, o limite
   acima do qual o firmware recusa a imagem.

### Universal x por dispositivo

O EPUB existe para se adaptar. Por isso há dois caminhos:

| | Universal (`generic_epub`, `manga_epub`) | Por dispositivo (`xteink_x4_pro`, `kindle_*`…) |
| --- | --- | --- |
| Tamanho da página | mantém a origem, limitada a um teto | dimensionado para a tela |
| Serve em | qualquer aparelho | um aparelho específico |
| Nitidez naquele aparelho | boa (o aparelho reduz) | melhor (redução Lanczos aqui) |
| Tamanho do arquivo | maior | menor |

## Outros perfis e por que são diferentes

| Perfil | Formato garantido | Quadrinhos | Observação |
| --- | --- | --- | --- |
| `generic_epub` | EPUB | EPUB de imagens | **universal**, mantém resolução e cor |
| `manga_epub` | EPUB | EPUB de imagens | universal com teto 2400 px, RTL |
| `kindle_paperwhite` | AZW3/EPUB | EPUB de imagens | Send to Kindle aceita EPUB desde 2022 |
| `kindle_legacy` | MOBI/AZW3 | EPUB de imagens | Kindles pré-2022 recebem MOBI via USB |
| `kobo_clara` / `kobo_libra` | KEPUB | **CBZ** | Kobo abre CBZ nativamente |
| `tablet` | EPUB/PDF | PDF | Tela grande, cor |

## Como adicionar/ajustar um dispositivo

Os perfis embutidos ficam em `app/devices/profiles.py`. Perfis personalizados
são criados pelo painel (tabela `device_profiles`) e sobrescrevem os embutidos.

Principais campos:

- `screen_width` / `screen_height`: pixels do painel.
- `grayscale` / `gray_levels`: converte para L / dithering quando `2`.
- `native_formats` / `preferred_format`: o que o aparelho abre sem conversão.
- `comic_output`: `epub_images` (EPUB de imagens), `cbz` ou `pdf`.
- `rotate_portrait`, `crop_margins`, `gamma`, `contrast`, `image_quality`.
- `max_file_size_mb`: teto de tamanho do arquivo gerado.

> Mantenha a regra do projeto: **um perfil só declara como nativo o que foi
> efetivamente verificado**. Comportamento de firmware muda; documente a fonte.
