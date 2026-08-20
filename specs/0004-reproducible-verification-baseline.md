# SPEC-0004 — Baseline reprodutível de testes e verificação

- **Status:** implementado; baseline canônico e verificação operacional concluídos
- **Versão:** 1.6
- **Prioridade/Fase:** P0/P1 / baseline e verificação operacional
- **Rastreabilidade:** PRD §9; ARCHITECTURE §13; `IMPLEMENTATION_PLAN.md` baseline concluído, discrepância de entrada de testes e evidência externa pendente; SPEC-0001–0003
- **Dependências:** SPEC-0001, SPEC-0002, SPEC-0003

## Status de implementação

O baseline foi implementado em 2026-08-09. Os módulos de teste
são rastreáveis, o `conftest.py` não seleciona um modo alternativo e
`test_ticket_closure.py` cobre o contrato de ciclos persistentes. O
runner `PYTHONPATH=/app python scripts/verify.py` cria PostgreSQL 16 descartável,
comprova a conexão do próprio processo, aplica e verifica Alembic head e
fornece a URL exata ao subprocesso PostgreSQL. A execução histórica mais
recente registrada nesta especificação (issue 0016, 2026-08-14) produziu **177 passed, 56 skipped** na
etapa offline, **56 passed, 177 deselected** na etapa PostgreSQL e zero
diagnósticos no Pyright. Os 48 skips pertencem apenas à etapa offline, onde o
banco deliberadamente não é fornecido.
Essa etapa offline não seleciona uma flag de finalização; o runner injeta a URL
do banco descartável somente na etapa PostgreSQL.

A execução canônica anterior, registrada no issue 0030 em 2026-08-20,
passou compileall, Pyright estrito, **215 testes offline e 69 skips**, Alembic
`0020_cycle_contact_provenance` e **69 testes PostgreSQL, 215 desselecionados**.
Os resultados continuam sendo evidência local/descartável e não comprovam
Redis, fornecedores, réplicas, deployment ou produção.

A execução canônica anterior, registrada no issue 0031 em 2026-08-20,
passou compileall, Pyright estrito, **216 testes offline e 69 skips**, Alembic
`0020_cycle_contact_provenance` e **69 testes PostgreSQL, 216 desselecionados**.
Os resultados continuam sendo evidência local/descartável e não comprovam
Redis, fornecedores, réplicas, deployment ou produção.

A execução canônica mais recente, registrada no issue 0032 em 2026-08-20,
passou compileall, Pyright estrito, **218 testes offline e 69 skips**, Alembic
`0020_cycle_contact_provenance` e **69 testes PostgreSQL, 218 desselecionados**.
Os resultados continuam sendo evidência local/descartável e não comprovam
Redis, fornecedores, réplicas, deployment ou produção.

A verificação operacional foi adicionada ao mesmo contrato
de seleção PostgreSQL. `tests/test_operational_recovery_db.py` usa transporte
de fila determinístico e identificadores sintéticos para verificar claim/lease
de ciclo, liberação após falha de publicação, agenda futura, recuperação de
áudio/imagem sem duplicação e despertar seletivo de ciclos bloqueados por
imagem. O issue 0016 também confirmou a migration head
`0018_department_mapping` e a cobertura de identidade, hydration, backfill de
contatos, resolução conservadora e mapeamento departamental; isso não comprova
Redis, fornecedores, réplicas ou produção.

## Objetivo e não objetivos

Fazer um checkout limpo conter e executar a suíte canônica sem `.env` pessoal, distinguindo verificações offline, PostgreSQL descartável e o teste live opt-in. Não altera comportamento de produção, não inicia serviços de produção e não torna `test_webhook_local.py` canônico.

## Estado de referência

O diretório contém os módulos `test_*.py` rastreáveis; `test_webhook_local.py`
é importável durante a coleta sem executar o smoke e só envia uma requisição
quando executado diretamente. O comando canônico não executa essa ação live. O
Compose de teste publica
PostgreSQL 16 em porta de host dinâmica (`published: 0`) e o runner usa um
projeto, rede e armazenamento temporários; em execução containerizada, usa o
hostname interno `postgres-test:5432` sem tocar em outros projetos Compose.

## Requisitos

1. Todos os arquivos de teste devem ser removidos do `.gitignore` e incluídos no próximo commit. A política de versionamento **deve** incluir as famílias canônicas offline e PostgreSQL; checkout limpo **não pode** depender de módulo de teste não rastreado.
2. Fixtures **devem** isolar configuração e não podem herdar `.env` pessoal. A suíte offline **deve** usar o único modo persistente suportado.
3. Cobertura de compatibilidade removida não pode reaparecer como dependência implícita; famílias distintas não podem compartilhar suposições implícitas de configuração.
4. Um runner versionado **deve** iniciar PostgreSQL 16 descartável, aplicar Alembic head, fornecer `CAI_TEST_DATABASE_URL` alcançável pelo processo de teste e evitar host/porta de banco de desenvolvimento ou produção. A fixture que faz `TRUNCATE` só pode apontar para esse alvo comprovadamente descartável.
5. O runner **deve** executar compileall em `src`, `tests` e `alembic`, Pyright estrito, testes offline e todas as famílias PostgreSQL. Cada etapa canônica **deve** falhar a automação em caso de erro; pass, skip e pré-requisito indisponível **devem** ser reportados separadamente. Skip não prova schema nem runtime.
6. `test_webhook_local.py` **deve** permanecer excluído, salvo quando uma API local for iniciada deliberadamente. Documentação versionada **deve** declarar comandos, ambiente, destino de banco e resultado esperado para cada classe de verificação.

## Aceitação

- [x] O runner local canônico `PYTHONPATH=/app python scripts/verify.py` executa compileall, `npx --yes pyright` sem diagnósticos e a suíte offline no único modo persistente suportado.
- [x] O banco descartável aplica Alembic head e executa os testes PostgreSQL a partir do mesmo processo de teste, usando a forma host publicada ou a forma interna Docker quando o runner está containerizado.
- [x] O runner fornece `CAI_TEST_DATABASE_URL` ao processo de teste para uma instância PostgreSQL 16 isolada e descartável.
- [x] A colisão da porta fixa 5433 em `docker-compose.test.yml` é resolvida com publicação dinâmica.
- [x] Nenhum comando canônico usa uma URL, volume ou porta de produção/desenvolvimento sem isolamento demonstrado.

## Decisão registrada

O runner local versionado é o mecanismo canônico antes de qualquer deploy. GitHub Actions ou serviço externo de CI pode ser adicionado futuramente, mas não é necessário agora. A lista canônica inclui compileall, Pyright sem diagnósticos, a suíte offline e a família PostgreSQL em PostgreSQL 16 descartável com `CAI_TEST_DATABASE_URL`, incluindo a fatia operacional. A execução mais recente registrada em 2026-08-14 passou nas etapas estáticas, offline, conectividade, Alembic e PostgreSQL; o teste live continua opt-in.
