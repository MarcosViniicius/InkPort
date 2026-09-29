# Gerador de arte (`tools/artwork`)

Gera a identidade visual versionada do projeto — banner, cartão social, marca,
badges e diagrama de arquitetura — **em SVG**, sem dependência externa e de
forma determinística. Você só edita textos/estrutura; o resto é derivado.

```text
artwork.toml   <-- o único arquivo que você edita
   |
   |  python -m tools.artwork.generate
   v
docs/images/banner.svg, social-card.svg, logo.svg, architecture.svg, badges/*.svg
```

## Como usar

```bash
# edite os textos/versão em tools/artwork/artwork.toml, então:
.\.venv\Scripts\python.exe -m tools.artwork.generate      # Windows
python -m tools.artwork.generate                          # Linux/macOS

# outras opções
python -m tools.artwork.generate --check                  # não escreve; falha se estiver desatualizado
python -m tools.artwork.generate --list                   # lista os artefatos
python -m tools.artwork.generate --version 0.2.0          # sobrescreve a versão
python -m tools.artwork.generate --out /tmp/arte          # grava sob outro diretório
```

A versão vem de `app/__init__.py` (`__version__`) quando não é informada — não
existe cópia da versão em lugar nenhum.

## O que é gerado

| Arquivo | Uso |
| --- | --- |
| `docs/images/banner.svg` | topo do README |
| `docs/images/social-card.svg` | imagem de compartilhamento (1200×630) |
| `docs/images/logo.svg` | marca (avatar/favicon) |
| `docs/images/architecture.svg` | diagrama no README e em `docs/architecture.md` |
| `docs/images/badges/*.svg` | badges (version, python, opds, docker) |

## De onde vem cada coisa (fonte única)

| Item | Origem | Por quê |
| --- | --- | --- |
| Cores e fontes | `app/web/static/style.css` (bloco escuro) | a arte nunca destoa da UI |
| Ícones | `app/web/templates/partials/icons.html` | o mesmo ícone do painel, sem cópia |
| Versão | `app/__init__.py::__version__` | uma só verdade |
| Textos e estrutura | `tools/artwork/artwork.toml` | é o que você edita |

## Configuração (`artwork.toml`)

| Chave | O que é |
| --- | --- |
| `[project] name/tagline/repo` | nome, assinatura e repositório (o repo vira `github.com/<repo>`) |
| `[project] version` | opcional; vazio = usa `app/__init__.py` |
| `[banner] chips` | etiquetas do banner (a linha tem limite de largura e o gerador avisa) |
| `[social] headline/bullets/formats` | textos do cartão social |
| `[[badges.items]]` | `label`, `value`, `color` (aceita `{version}` e `{name}`) |
| `[diagram] title/width/arrows` | título, largura e um rótulo por seta (entre tiers consecutivos) |
| `[[diagram.tier]]` | um nível: `label`, `band` (faixa de fundo) e `kind` (`cards` ou `chips`) |
| `[[diagram.tier.cards]]` | `title`, `mono` (linha monoespaçada), `lines`, `accent`, `highlight` |

`chips` de um tier usa `chips = [...]` (grade de 3 colunas; altere
`CHIP_COLUMNS` em `diagram.py`).

## Como estender

| Quero… | Faça |
| --- | --- |
| Mudar um texto | edite `artwork.toml` e rode o gerador |
| Novo badge | acrescente `[[badges.items]]` (o arquivo é `badges/<label>.svg`) |
| Novo nível no diagrama | acrescente `[[diagram.tier]]` + um rótulo em `arrows` |
| Nova arte (ex. "poster") | crie `tools/artwork/poster.py` com `build(config) -> Svg` e registre em `generate.build_all` |
| Nova métrica/figura | use as primitivas de `svg.py` (`rect`, `text`, `chip`, `card`, `arrow_down`, `glow`) |
| Mais um mockup | `mockups.py` (ex.: `device`, `cover_stack`) |
| Outra cor derivada | `palette.mix(a, b, ratio)` |

## Invariantes (não quebre)

- **SVG autocontido**: sem `<script>`, sem `<style>`, sem `http(s)://`, sem
  fonte externa. Só atributos de apresentação — assim renderiza igual no
  navegador, no GitHub e dentro de um `<img>`.
- **Determinístico**: a mesma config produz o mesmo arquivo, byte a byte (é o que
  permite `--check` e o teste de deriva).
- **Nada de número mágico de texto**: toda caixa verifica se o rótulo cabe; se
  não couber, o gerador emite um **aviso** e `--check` falha. Nunca deixe um
  aviso passar.
- **Arte versionada**: os SVG gerados vão para o git junto com a config.

## Verificar

```bash
python -m tools.artwork.generate --check   # nada desatualizado, nenhum aviso
python tests/artwork.py                    # XML, dimensões, autocontenção e deriva
ruff check tools
```

Para conferir o desenho de verdade, renderize e **olhe**:

```bash
chrome --headless=new --window-size=1280,300 \
  --screenshot=banner.png file:///.../docs/images/banner.svg
```

## Armadilhas

- **SVG não mede texto.** As larguras são estimadas (`svg.text_width`) com folga
  proposital; um aviso de "não cabe" pode ser falso positivo — reduza a fonte ou
  encurte o texto em vez de ignorar.
- **Cache do Chrome** ao renderizar para conferir: use um `--user-data-dir`
  temporário ou apague o perfil entre execuções.
- **`font-family` com aspas**: as pilhas do CSS entram com aspas simples no
  atributo; não troque para aspas duplas sem escapar.
- Não edite `docs/images/*.svg` à mão: a próxima geração sobrescreve (e o teste
  de deriva reprova).

## Para modelos de IA

1. Leia `artwork.toml` antes de mexer no código — quase sempre a mudança é lá.
2. Se for código, siga o padrão dos módulos: uma responsabilidade por arquivo,
   docstring/comentário **em inglês**, texto de interface **em pt-BR**.
3. Nunca invente campos de configuração nem cores: use `palette.py` (que lê o
   CSS) e `svg.py` (que checa o encaixe).
4. Depois de qualquer mudança: `python -m tools.artwork.generate`,
   `python tests/artwork.py`, `ruff check tools` — e **olhe** o SVG renderizado
   antes de commitar.
