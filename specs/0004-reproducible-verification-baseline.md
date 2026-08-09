# SPEC-0004 — Baseline reprodutível de testes e verificação

- **Status:** item 1 implementado; runner PostgreSQL do item 2 pendente
- **Versão:** 1.1
- **Prioridade/Fase:** P0 / fase 0
- **Rastreabilidade:** PRD §9; ARCHITECTURE §13; `IMPLEMENTATION_PLAN.md` itens 1–4; SPEC-0001–0003
- **Dependências:** SPEC-0001, SPEC-0002, SPEC-0003

## Status de implementação

O item 1 da fase 0 foi implementado em 2026-08-09. Os 27 módulos de teste são
rastreáveis, o `conftest.py` seleciona o modo persistente sem herdar a flag do
`.env` e `test_ticket_closure.py` cobre o contrato de ciclos persistentes. O
comando offline canônico, executado com a flag externa tanto em `true` quanto em
`false`, produziu **120 passed, 28 skipped** em cada execução. Os 28 skips são
famílias PostgreSQL sem `CAI_TEST_DATABASE_URL`; o runner descartável, a
conectividade e a verificação de migrations continuam no item 2.

## Objetivo e não objetivos

Fazer um checkout limpo conter e executar a suíte canônica sem `.env` pessoal, distinguindo verificações offline, PostgreSQL descartável e o teste live opt-in. Não altera comportamento de produção, não inicia serviços de produção e não torna `test_webhook_local.py` canônico.

## Estado de referência

O diretório contém 27 módulos `test_*.py` rastreáveis. O comando canônico
exclui deliberadamente `test_webhook_local.py`, coleta 148 testes e, sem
`CAI_TEST_DATABASE_URL`, produz 120 aprovados e 28 ignorados. O Compose de teste
fixa a porta de host 5433 e ainda não é um runner portátil comprovado.

## Requisitos

1. Todos os arquivos de teste devem ser removidos do `.gitignore` e incluídos no próximo commit. A política de versionamento **deve** incluir as famílias canônicas offline e PostgreSQL; checkout limpo **não pode** depender de módulo de teste não rastreado.
2. Fixtures **devem** isolar configuração e não podem herdar `.env` pessoal. A suíte offline **deve** definir explicitamente `DIGISAC_HISTORY_FINALIZATION_ENABLED` para o modo persistente, e não herdá-lo do `.env`.
3. Cobertura legada que dependia de `DIGISAC_HISTORY_FINALIZATION_ENABLED=false` deve ser removida ou substituída por cobertura persistente rastreada. Famílias distintas não podem compartilhar suposições implícitas de configuração.
4. Um runner versionado **deve** iniciar PostgreSQL 16 descartável, aplicar Alembic head, fornecer `CAI_TEST_DATABASE_URL` alcançável pelo processo de teste e evitar host/porta de banco de desenvolvimento ou produção. A fixture que faz `TRUNCATE` só pode apontar para esse alvo comprovadamente descartável.
5. O runner **deve** executar compileall em `src`, `tests` e `alembic`, Pyright estrito, testes offline e todas as famílias PostgreSQL. Cada etapa canônica **deve** falhar a automação em caso de erro; pass, skip e pré-requisito indisponível **devem** ser reportados separadamente. Skip não prova schema nem runtime.
6. `test_webhook_local.py` **deve** permanecer excluído, salvo quando uma API local for iniciada deliberadamente. Documentação versionada **deve** declarar comandos, ambiente, destino de banco e resultado esperado para cada classe de verificação.

## Aceitação

- O runner local canônico (por exemplo, `./run_tests.sh`) executa `python -m compileall -q src tests alembic`, `npx --yes pyright` sem diagnósticos e a suíte offline com `DIGISAC_HISTORY_FINALIZATION_ENABLED` explicitamente definido, não herdado do `.env`.
- O banco descartável aplica Alembic head e executa os testes PostgreSQL a partir do processo de teste, não apenas de dentro do container.
- O runner fornece `CAI_TEST_DATABASE_URL` ao processo de teste para uma instância PostgreSQL 16 isolada e descartável.
- A colisão da porta fixa 5433 em `docker-compose.test.yml` é resolvida para que o PostgreSQL descartável seja acessível pelo processo de teste.
- Nenhum comando canônico usa uma URL, volume ou porta de produção/desenvolvimento sem isolamento demonstrado.

## Decisão registrada

O runner local versionado é o mecanismo canônico antes de qualquer deploy. GitHub Actions ou serviço externo de CI pode ser adicionado futuramente, mas não é necessário agora. A lista canônica inclui compileall, Pyright sem diagnósticos, a suíte offline com flag explícita e a família PostgreSQL em PostgreSQL 16 descartável com `CAI_TEST_DATABASE_URL`.
