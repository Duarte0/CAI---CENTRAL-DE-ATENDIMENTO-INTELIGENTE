# SPEC-0004 — Baseline reprodutível de testes e verificação

- **Status:** itens 1 e 2 implementados; verificação operacional do item 4 concluída
- **Versão:** 1.3
- **Prioridade/Fase:** P0/P1 / baseline e verificação operacional
- **Rastreabilidade:** PRD §9; ARCHITECTURE §13; `IMPLEMENTATION_PLAN.md` itens 1–4; SPEC-0001–0003
- **Dependências:** SPEC-0001, SPEC-0002, SPEC-0003

## Status de implementação

Os itens 1 e 2 da fase 0 foram implementados em 2026-08-09. Os módulos de teste
são rastreáveis, o `conftest.py` seleciona o modo persistente sem herdar a flag
do `.env` e `test_ticket_closure.py` cobre o contrato de ciclos persistentes. O
runner `PYTHONPATH=/app python scripts/verify.py` cria PostgreSQL 16 descartável,
comprova a conexão do próprio processo, aplica e verifica Alembic head e
fornece a URL exata ao subprocesso PostgreSQL. A execução observada produziu
**128 passed, 33 skipped** na etapa offline, **33 passed, 128 deselected** na
etapa PostgreSQL e zero diagnósticos no Pyright. Os 33 skips pertencem apenas à
etapa offline, onde o banco deliberadamente não é fornecido.

A verificação operacional do item 4 do plano foi adicionada ao mesmo contrato
de seleção PostgreSQL. `tests/test_operational_recovery_db.py` usa transporte
de fila determinístico e identificadores sintéticos para verificar claim/lease
de ciclo, liberação após falha de publicação, agenda futura, recuperação de
áudio/imagem sem duplicação e despertar seletivo de ciclos bloqueados por
imagem. A execução observada produziu **33 passed, 128 deselected** no destino
PostgreSQL 16 descartável; isso não comprova Redis, fornecedores, réplicas ou
produção.

## Objetivo e não objetivos

Fazer um checkout limpo conter e executar a suíte canônica sem `.env` pessoal, distinguindo verificações offline, PostgreSQL descartável e o teste live opt-in. Não altera comportamento de produção, não inicia serviços de produção e não torna `test_webhook_local.py` canônico.

## Estado de referência

O diretório contém os módulos `test_*.py` rastreáveis e o comando canônico
exclui deliberadamente `test_webhook_local.py`. O Compose de teste publica
PostgreSQL 16 em porta de host dinâmica (`published: 0`) e o runner usa um
projeto, rede e armazenamento temporários; em execução containerizada, usa o
hostname interno `postgres-test:5432` sem tocar em outros projetos Compose.

## Requisitos

1. Todos os arquivos de teste devem ser removidos do `.gitignore` e incluídos no próximo commit. A política de versionamento **deve** incluir as famílias canônicas offline e PostgreSQL; checkout limpo **não pode** depender de módulo de teste não rastreado.
2. Fixtures **devem** isolar configuração e não podem herdar `.env` pessoal. A suíte offline **deve** definir explicitamente `DIGISAC_HISTORY_FINALIZATION_ENABLED` para o modo persistente, e não herdá-lo do `.env`.
3. Cobertura legada que dependia de `DIGISAC_HISTORY_FINALIZATION_ENABLED=false` deve ser removida ou substituída por cobertura persistente rastreada. Famílias distintas não podem compartilhar suposições implícitas de configuração.
4. Um runner versionado **deve** iniciar PostgreSQL 16 descartável, aplicar Alembic head, fornecer `CAI_TEST_DATABASE_URL` alcançável pelo processo de teste e evitar host/porta de banco de desenvolvimento ou produção. A fixture que faz `TRUNCATE` só pode apontar para esse alvo comprovadamente descartável.
5. O runner **deve** executar compileall em `src`, `tests` e `alembic`, Pyright estrito, testes offline e todas as famílias PostgreSQL. Cada etapa canônica **deve** falhar a automação em caso de erro; pass, skip e pré-requisito indisponível **devem** ser reportados separadamente. Skip não prova schema nem runtime.
6. `test_webhook_local.py` **deve** permanecer excluído, salvo quando uma API local for iniciada deliberadamente. Documentação versionada **deve** declarar comandos, ambiente, destino de banco e resultado esperado para cada classe de verificação.

## Aceitação

- [x] O runner local canônico `PYTHONPATH=/app python scripts/verify.py` executa compileall, `npx --yes pyright` sem diagnósticos e a suíte offline com `DIGISAC_HISTORY_FINALIZATION_ENABLED` explicitamente definido, não herdado do `.env`.
- [x] O banco descartável aplica Alembic head e executa os testes PostgreSQL a partir do mesmo processo de teste, usando a forma host publicada ou a forma interna Docker quando o runner está containerizado.
- [x] O runner fornece `CAI_TEST_DATABASE_URL` ao processo de teste para uma instância PostgreSQL 16 isolada e descartável.
- [x] A colisão da porta fixa 5433 em `docker-compose.test.yml` é resolvida com publicação dinâmica.
- [x] Nenhum comando canônico usa uma URL, volume ou porta de produção/desenvolvimento sem isolamento demonstrado.

## Decisão registrada

O runner local versionado é o mecanismo canônico antes de qualquer deploy. GitHub Actions ou serviço externo de CI pode ser adicionado futuramente, mas não é necessário agora. A lista canônica inclui compileall, Pyright sem diagnósticos, a suíte offline com flag explícita e a família PostgreSQL em PostgreSQL 16 descartável com `CAI_TEST_DATABASE_URL`, incluindo a fatia operacional do item 4. A execução observada em 2026-08-09 passou nas etapas estáticas, offline, conectividade, Alembic e PostgreSQL; o teste live continua opt-in.
