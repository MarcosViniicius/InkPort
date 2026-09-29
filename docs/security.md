# Segurança

Modelo de segurança, resultado da auditoria e como endurecer uma instalação
exposta. Escrito para humanos e para agentes de IA.

## Princípio

**Tudo passa por credencial.** O painel, a API e o catálogo OPDS exigem
autenticação; nada de conteúdo é servido sem ela. As exceções são deliberadas e
mínimas: `/health` (para healthcheck) e `/static` (CSS/JS, sem dado nenhum).

## Modelo

| Superfície | Proteção |
| --- | --- |
| Painel (`/`, `/library`, `/import`, `/settings`, …) | sessão (cookie assinado); sem sessão → `/login` |
| API (`/api/**`) | sessão; sem sessão → `401` |
| Especificação (`/api/openapi.json`) | sessão |
| Leitor web (`/reader/**`) | sessão |
| **Catálogo OPDS** (`/opds`, `/opds/v2`, capas, downloads) | **HTTP Basic**, ligado por padrão |
| Primeiro acesso (`/setup`) | token sorteado no boot (obrigatório fora da rede local) |
| Banco de dados | cifrado em repouso (SQLCipher), chave em `DATA_DIR/secret.key` |
| Segredo de sessão | aleatório persistido em `DATA_DIR/session.key` (0600) |

Senhas: PBKDF2-SHA256, 240 000 iterações, salt de 16 bytes, comparação com
`hmac.compare_digest`. Nem o painel nem o banco guardam senha em texto puro.

## Auditoria (achados e situação)

| # | Achado | Severidade | Situação |
| --- | --- | --- | --- |
| 1 | `SECRET_KEY` de fábrica (público no `.env.example`) permitia forjar o cookie de sessão | **crítica** | ✅ corrigido: sem `SECRET_KEY`, um segredo aleatório é gerado e persistido em `DATA_DIR/session.key` (0600), com aviso no log |
| 2 | `/setup` podia ser assumido por qualquer pessoa que chegasse primeiro (instalação nova em IP público) | **crítica** | ✅ corrigido: fora da rede local o assistente exige token (mostrado no log do boot) |
| 3 | Catálogo OPDS aberto por padrão | **alta** | ✅ corrigido: `opds_require_auth` ligado por padrão; sem credencial o catálogo responde `401` e o painel mostra o aviso |
| 4 | Cookie de sessão sem `Secure` | **alta** | ✅ corrigido: `Secure` automático quando `BASE_URL` é `https://`; HSTS nas respostas por HTTPS |
| 5 | Login sem freio (força bruta) | **média** | ✅ corrigido: 5 falhas por `usuário@ip` bloqueiam por 5 min (`security/throttle.py`), com atraso fixo por falha e registro no log |
| 6 | Sem cabeçalhos de segurança | **média** | ✅ corrigido: CSP, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy` |
| 7 | `WWW-Authenticate` era descartado no `401` do OPDS: o leitor e-ink **não pedia** a senha, só falhava | **alta** (funcional) | ✅ corrigido: o handler de erro preserva os cabeçalhos da exceção |
| 8 | Redirecionamento aberto no login (`next="//outro.site"`) | baixa | ✅ corrigido: `auth.safe_next` só aceita caminho interno |
| 9 | Documentação da API (`/api/docs` + OpenAPI) exposta sem login — e o Swagger UI carrega JS de CDN | baixa | ✅ corrigido: UI removida (projeto sem CDN); a especificação ficou em `/api/openapi.json`, atrás do login |
| 10 | Versão do servidor em `/health` | informativa | mantido: o projeto é público e o healthcheck precisa do endpoint |
| 11 | Sem CSRF token nos formulários | baixa | mitigado por `SameSite=Lax` (o navegador não envia o cookie em POST de outro site) |
| 12 | Freio de força bruta é em memória (zera ao reiniciar, não vale com múltiplos processos) | limitação | documentado: use rate limit no proxy se expor de verdade |

Cobertura automatizada: `tests/security.py` (57 checagens) — segredo de sessão,
token do assistente, catálogo com/sem credencial, hash de senha, freio, destinos
internos, cabeçalhos e API atrás do login.

## Endurecer uma instalação exposta

1. **HTTPS primeiro.** Ponha um proxy com TLS (Caddy é 3 linhas) e defina
   `BASE_URL=https://seu.dominio` — isso liga o cookie `Secure` e o HSTS.
2. **Credencial do OPDS**: defina usuário e senha em Configurações (obrigatório
   enquanto “exigir credencial” estiver ligado). No leitor, o catálogo precisa
   dessas credenciais.
3. **Senha forte** no painel (Configurações → **Usuário e senha do painel**) e
   **troque** sempre que ela passar por um canal inseguro (chat, e-mail, print).
4. **Feche a porta** para o mundo quando não precisar: firewall por IP, VPN
   (Tailscale/WireGuard) ou proxy com autenticação.
5. **Backup com a chave**: `data/` inteiro (banco cifrado + `secret.key` +
   `session.key`). Sem a chave, o banco não abre.
6. Não versione `.env`, `secret.key`, `session.key` nem `data/` (o `.gitignore`
   já cobre).

## O que este projeto NÃO tem (limites honestos)

- Um único usuário (sem papéis nem multiusuário).
- Sem 2FA/TOTP e sem “lembrar dispositivo”.
- Sem trilha de auditoria das ações administrativas (só o log da aplicação).
- Sem revogação de sessão além de trocar a senha / limpar o cookie.
- Sem proteção anti-DDoS: o freio do login é local e por processo.
- Sem verificação de integridade dos arquivos da biblioteca (hash existe para
  deduplicação, não para segurança).

## Invariantes para quem for mexer

- **Nunca** sirva conteúdo (feed, capa, arquivo, capítulo, API) sem passar por
  `require_panel`, `require_api` ou `require_opds_auth`.
- Preserve `WWW-Authenticate` em respostas `401` de superfícies de máquina — sem
  ele o leitor não sabe pedir a senha.
- Não aceite `SECRET_KEY` de fábrica, nem grave segredo em texto puro: use
  `security/runtime.py` (segredos viram hash) e os arquivos 0600 em `DATA_DIR`.
- Redirecionamentos só para caminho interno (`auth.safe_next`).
- `/setup` continua atrás de token para quem não é local; `/health` e `/static`
  são as únicas rotas públicas — pense duas vezes antes de acrescentar outra.
