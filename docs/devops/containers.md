# Containers: auditoria de RAM e disco

Como auditar e otimizar os containers Linux deste projeto **medindo**, sem
quebrar a aplicação. Escrito para humanos e para agentes de IA.

## O que existe

| Peça | Caminho | Papel |
| --- | --- | --- |
| Agente | `.opencode/agents/container-optimizer.md` | DevOps/SRE sênior; método, playbook e guardrails |
| Comando | `.opencode/commands/container-audit.md` | `/container-audit` dispara a auditoria pelo agente |
| Ferramenta | `tools/devops/container_report.py` | Mede imagem, camadas, conteúdo, RAM e disco |

## Como usar

**1. Pelo comando (mais rápido).** No TUI, digite:

```text
/container-audit
/container-audit foque só na imagem
```

**2. Pelo agente principal.** Peça em linguagem natural:

```text
Use o subagent container-optimizer para auditar os containers deste repositório.
```

**3. Como agente principal.** Selecione `container-optimizer` na lista de agentes
do TUI e converse direto com ele (ele tem `mode: all`: roda sozinho ou como
subagente).

## Medir

```bash
# descobre imagem e container pelo docker-compose.yml
python tools/devops/container_report.py

# explícito
python tools/devops/container_report.py inkport:latest inkport

# resumo legível por máquina (para diff antes/depois)
python tools/devops/container_report.py inkport:latest inkport --json

# podman
CONTAINER_RUNTIME=podman python tools/devops/container_report.py
```

O script não tem dependências (stdlib + CLI do docker/podman), roda igual no
Windows (Docker Desktop), no WSL e num servidor Linux, e **não altera nada**:
usa `run --rm --network none` para inspecionar o conteúdo da imagem.

### O que cada número significa

| Medida | Onde ler | Por que importa |
| --- | --- | --- |
| Tamanho descompactado | `docker image inspect .Size` | é o que ocupa disco no host |
| Camadas | `RootFS.Layers` | cada `RUN`/`COPY` é uma camada; limpar depois **não** reduz |
| Maiores diretórios | `du -xhd1 /` na imagem | mostra o que dá para tirar (`/usr`, `/app`) |
| `memory.current` / `memory.peak` | cgroup do container | RAM real e pico (inclui page cache) |
| `memory.stat anon` vs `file` | cgroup | separa heap de cache: cache **não** é vazamento |
| `SizeRw` | `docker inspect` | quanto o container escreveu na camada gravável |
| Driver/opções de log | `HostConfig.LogConfig` | `json-file` sem `max-size` cresce sem fim |

## Linha de base (este repositório)

Medida em 2026-09-29 com
`python tools/devops/container_report.py inkport:latest inkport-baseline`
(Docker Desktop 29.6.2, linux/amd64, base `python:3.12-slim` / Debian trixie).
Preencha de novo depois de cada mudança e compare — os números ficam aqui.

| Métrica | Antes | Depois | Δ | Verificado com |
| --- | --- | --- | --- | --- |
| Imagem (`docker images`) | 482 MB | — | — | `image ls` |
| Ocupado em disco (`du /`) | 340 MB | — | — | `du -xhd1 /` |
| Camadas | 13 | — | — | `RootFS.Layers` |
| Maior camada | **208 MB** (`pip install -r requirements.txt`) | — | — | `docker history` |
| Camada do `curl` + apt | **13,5 MB** | — | — | `docker history` |
| `/app` dentro da imagem | 5,3 MB | — | — | `du -xhd1 /` |
| Usuário | **root** | — | — | `image inspect` |
| RAM em repouso (`anon`) | 70,9 MB (pico 77,8 MB) | — | — | `memory.stat` |
| Processos em repouso | 7–8 pids | — | — | `pids.current` |
| Rotação de log | **ausente** (`json-file`) | — | — | `LogConfig` |

> **Cuidado ao comparar tamanhos.** O mesmo build aparece como 115,3 MB
> (`.Size`), 482 MB (`docker images`, soma das camadas) e 340 MB (`du /`, o que
> ocupa em disco). Cada número mede uma coisa diferente — o store atual informa
> `.Size` comprimido. **Escolha um método e use o mesmo antes e depois**; para
> "quanto ocupa de verdade", o `du` dentro da imagem é o mais fiel.

### Candidatos confirmados pela medição

Situação em 2026-09-29. **Os deltas são esperados, não remedidos**: o build e a
medição seguintes ficaram pendentes (sem Docker disponível no momento) — rode
`python tools/devops/container_report.py` depois do próximo `--build` para
fechar os números.

| Candidato | Ganho medido/esperado | Situação |
| --- | --- | --- |
| Healthcheck sem `curl` (usar `python -c`) | elimina a camada de **13,5 MB** | ✅ feito no `Dockerfile` |
| Não copiar `tests/`, `docs/`, `.env.example` à imagem | ~1,7 MB dos 5,3 MB de `/app` | ✅ feito |
| `.dockerignore` cobrindo `tests/docs/tools/.opencode` | build mais rápido | ✅ feito |
| Rotação de log (`logging.options.max-size`) | contém o crescimento **ilimitado** de disco | ✅ feito no `docker-compose.yml` |
| `USER` non-root | menos superfície | pendente |
| `--mount=type=cache` no pip | rebuild mais rápido (208 MB de dependências) | pendente |
| `init: true` + limites de memória/CPU | picos de conversão sob controle | pendente |

O `pip install` (208 MB) é o grosso da imagem e **é o app**: não dá para "cortar"
sem perder formato. O ganho real está em tirar o que não é runtime, na camada do
`curl`, na rotação de log e em separar build de runtime.

## Playbook (resumo operacional)

### Imagem e disco

1. Tirar do runtime o que não é runtime (testes, docs, caches).
2. Healthcheck sem `curl` — usar o próprio Python.
3. Limpar **na mesma** camada (`apt-get update && install && rm -rf /var/lib/apt/lists/*`).
4. Ordem de camadas para cache: dependências → código.
5. Multi-stage só quando há compilação de verdade.
6. `.dockerignore` completo.
7. Base: `slim` já é enxuta; `alpine`/`distroless` **só com teste** (rodas do PyMuPDF/Pillow, healthcheck).
8. Pinar versões, rotular com labels OCI.
9. Non-root sempre que possível.
10. Nada de segredo nem dado (`DATA_DIR`) na imagem.

### RAM e runtime

1. `docker stats` mistura cache com RSS — olhe `memory.stat` (`anon` vs `file`).
2. **Concorrência** é o botão principal (`CONVERSION_CONCURRENCY`, threads de imagem).
3. **Um único processo**: workers do uvicorn > 1 duplicariam os loops de background.
4. Heap: `MALLOC_ARENA_MAX` e similares só com medição de `anon`.
5. Limitar entrada (upload/tamanho de PDF) e observar `memory.events` por OOM.
6. SQLite no volume; `cache_size`/`mmap_size` conforme o host.

### Disco em runtime

1. **Rotação de log** (maior impacto diário).
2. Temporários em volume/tmpfs adequado, com limpeza ativa.
3. Medir `data/`; `VACUUM` no SQLite após muitas exclusões.
4. Não escrever em `/app` (dado é no volume).
5. Só `docker image prune` (sem `-a`); nunca prune global.

## Guardrails (o que o agente não faz)

Definidos no frontmatter do agente (`permissions`) e nas regras de ouro:

| Regra | Efeito | Motivo |
| --- | --- | --- |
| `subagent *` | deny | o especialista não abre subagentes (foco, sem fan-out) |
| `docker system prune` / `docker system prune *` | deny | apaga imagens/cache **de todos** os projetos |
| `docker volume prune *` / `docker volume rm *` | deny | pode destruir dados |
| `docker compose down -v*` | deny | `-v` apaga volumes |
| `docker rm/rmi *`, `image/container/builder prune *` | ask | destrutivo dentro do projeto: confirme |
| `sudo *` | ask | evita escalar privilégio sem intenção |

> A lista é um **cinto de segurança**, não um sandbox: o casamento de padrões em
> shell é aproximado. O guardrail de verdade é o sistema do agente (medir,
> preservar comportamento, não destruir, perguntar antes de arriscar).

## Para modelos de IA: como estender

**Onde mexer**

| Quero… | Edite |
| --- | --- |
| Mudar o método/regras do agente | `.opencode/agents/container-optimizer.md` (frontmatter = campos V2; corpo = system prompt) |
| Mudar as regras de permissão | frontmatter `permissions` (lista ordenada; **a última regra que casa vence**) |
| Criar um comando novo | `.opencode/commands/<nome>.md` com `agent:`/`description:` e o corpo como template |
| Medir mais coisa | `tools/devops/container_report.py` (funções `image_report`, `container_report`, `contents_report`) |
| Registrar resultados | este arquivo, na tabela "Linha de base" |

**Convenções obrigatórias**

- Nada de dependência externa: o script usa **stdlib** e a CLI do docker/podman.
- Comentários/docstrings em inglês; texto de interface em pt-BR.
- Arquivos pequenos, uma responsabilidade cada um.
- Não invente campos de configuração do OpenCode: V2 usa `agents`,
  `commands`, `permissions` com `action`/`resource`/`effect`. Campos legados
  (`tools`, `permission`, `temperature`, `maxSteps`) **não** entram.
- O agente tem `mode: all`; se mudar para `subagent`, o comando passa a rodar em
  sessão filha automaticamente.

**Definição de pronto** (qualquer mudança feita com este pacote)

- Linha de base e medição final com o **mesmo** método.
- Build limpo, `/health` OK, fluxo real exercitado (importar → converter → OPDS →
  leitor) e testes pertinentes verdes.
- Cada mudança explicada com o número que a justifica; o que não deu ganho foi
  revertido e registrado em "Descartado".
- Nada destrutivo executado; nada fora do repositório alterado.
