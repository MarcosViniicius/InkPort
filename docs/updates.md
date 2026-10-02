# Atualizações do InkPort

O servidor monitora o repositório git e avisa no painel quando há commits
novos, mostra o que mudou e aplica com um clique (`git pull`, com rebuild dos
containers no Docker).

## Como funciona

1. **Verificação** — o loop de manutenção compara o commit local (`git rev-parse
   HEAD`) com a branch monitorada no remoto (`git ls-remote`), no ritmo
   configurado. Nada é baixado nessa etapa.
2. **Aviso** — se o remoto está à frente, um aviso aparece no topo do painel com
   o botão **Detalhes**, que leva à seção *Configurações → Atualizações*.
3. **Detalhes** — a seção lista os commits pendentes (hash curto, mensagem,
   autor, data) e o estado da última verificação/aplicação.
4. **Aplicar** — o botão **Atualizar agora** roda em segundo plano: confere se a
   árvore está limpa e se o checkout está na branch monitorada, faz
   `git pull --ff-only` e:
   - no Docker, tenta `docker compose up -d --build` e mostra o resultado;
   - fora do Docker, pede para **reiniciar o servidor** (o processo em execução
     ainda tem o código antigo em memória).

O resultado de cada etapa fica gravado e visível na seção, mesmo se algo falhar.

## Configuração (Configurações → Atualizações)

| Campo | Padrão | Para quê |
|---|---|---|
| Verificar automaticamente | ligado | monitoramento pelo loop de manutenção |
| Branch monitorada | `main` | qual branch acompanhar |
| URL do repositório | vazio (= `origin`) | `https://` do repositório, ou `file://` local |
| Verificar a cada (horas) | 6 | intervalo mínimo entre verificações |

## No Docker (VPS)

A imagem **não leva o `.git` nem o binário do git**, então o monitor usa o
**commit gravado na build**: o `Dockerfile` aceita `--build-arg GIT_SHA=…` e o
`docker-compose.yml` repassa `${GIT_SHA}`. Os atalhos já preenchem sozinhos:

```bash
make docker            # Linux/macOS (exporta GIT_SHA do git antes do build)
.\tasks.ps1 docker     # Windows
```

Manualmente: `GIT_SHA=$(git rev-parse HEAD) docker compose up -d --build` (ou
defina a variável `GIT_SHA` no ambiente antes do compose).

Com o SHA na imagem, a **verificação** funciona igual à do código-fonte (a
comparação usa a API do GitHub via `httpx`; nenhuma dependência nova), o aviso
aparece no painel e a seção mostra os commits. O que **não** dá para fazer de
dentro do container é aplicar: a seção mostra as instruções (no host,
`git pull` + `docker compose up -d --build`) e um botão de aplicar só aparece
quando existe um checkout git de verdade.

Se a imagem foi construída sem `GIT_SHA`, a seção explica isso e pede para
reconstruir com o argumento — é o único caso em que o aviso não aparece.

## Limites honestos

- **Sem git ou sem checkout, sem mágica.** A aplicação roda em segundo plano e
  nunca trava a página: verificação e aplicação rodam em worker thread, e a
  seção mostra o último resultado.
- **A imagem Docker não leva o `.git`** (`.dockerignore`), mas leva o commit da
  build (`GIT_SHA`): com ele o aviso funciona; aplicar continua no host. Sem o
  `GIT_SHA`, a seção explica que é preciso reconstruir a imagem.
- **Só fast-forward.** Com alterações locais não salvas, ou fora da branch
  monitorada, a aplicação recusa e diz o motivo em vez de tentar um merge.
- **Sem shell.** Todos os comandos usam argv fixo com timeout; branch e URL vêm
  validados da configuração.
