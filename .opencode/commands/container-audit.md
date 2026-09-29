---
description: Audita e otimiza os containers do projeto (RAM e disco) com medição antes/depois
agent: container-optimizer
---

Faça uma auditoria de containers neste repositório com foco em **disco** e
**RAM**, seguindo o método do agente:

1. **Contexto** — leia `Dockerfile`, `docker-compose.yml`, `.dockerignore` e
   entenda como o app roda (um processo com workers em memória, SQLite no volume).
2. **Linha de base** — rode `python tools/devops/container_report.py` e guarde a
   saída (imagem, camadas, maiores diretórios e, se houver container, RAM).
3. **Diagnóstico** — tabela `item | ganho estimado | risco | esforço | como verificar`,
   do melhor custo/benefício para o pior.
4. **Aplicar** — uma mudança por vez, medindo de novo após cada uma; reverta o
   que não trouxer ganho mensurável.
5. **Verificar e relatar** — build + `/health` + fluxo real (importar, converter,
   OPDS, leitor) + testes pertinentes, e entregue a tabela antes/depois.

Não execute nada destrutivo (nada de `prune` global, `volume rm`,
`compose down -v`) e não altere nada fora deste repositório.

Escopo, se informado: $ARGUMENTS
