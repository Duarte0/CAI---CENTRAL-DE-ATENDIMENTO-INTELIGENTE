# SPEC-0005 — Reconciliação do baseline documental

- **Status:** implementado em 2026-08-14; evidências anteriores preservadas como históricas
- **Versão:** 1.3
- **Prioridade/Fase:** P1 / reconciliação documental
- **Rastreabilidade:** PRD §§5.4, 7 e 9; ARCHITECTURE §§10 e 13; `IMPLEMENTATION_PLAN.md` baseline concluído; issues 0006–0009 e 0012 (evidência registrada)
- **Dependências:** SPEC-0002, SPEC-0003, SPEC-0004

## Objetivo e não objetivos

Reconciliar a documentação derivada da implementação com o fluxo persistente
único, as rotas efetivamente montadas e a última evidência local registrada de
verificação. O resultado deve permitir que um operador ou mantenedor execute o
baseline canônico sem inferir uma flag removida, uma rota não montada ou
evidência de produção.

Esta especificação não autoriza alteração de código, testes, migrations,
configuração, rotas, política de versão de API, credenciais, infraestrutura ou
execução contra ambiente externo. Também não transforma a evidência local em
SLA, CI hospedada ou aprovação de produção.

## Estado de referência e divergências históricas corrigidas

O código monta consultas de conversa/ciclo sem prefixo de versão e mantém o
fluxo de finalização persistente como único caminho. O runner canônico não
habilita uma flag de finalização: ele remove `CAI_TEST_DATABASE_URL` da etapa
offline e fornece uma URL descartável somente à etapa PostgreSQL.

As divergências que motivaram esta especificação foram corrigidas nesta
reconciliação:

1. O cabeçalho de SPEC-0002 foi alinhado ao contrato e às rotas montadas sem
   versão.
2. SPEC-0004, PRD, ARCHITECTURE, README e o índice registram a evidência mais
   recente do issue 0012: **143 passed, 36 skipped** e **36 passed, 143
   deselected**. A evidência do issue 0008 (**127/33** e **33/127**) e a do
   issue 0007 (**122/33** e **33/122**) permanecem como histórico datado.
3. README não apresenta mais o status da conversa como fluxo legado nem afirma
   que o runner ativa explicitamente um modo persistente.

## Requisitos documentais e de compatibilidade

1. A documentação ativa **deve** declarar que a finalização persistente por
   histórico DigiSac é o único caminho suportado. Nenhuma rota, status,
   configuração, fila ou instrução operacional pode apresentar fluxo legado
   como comportamento atual.
2. A documentação ativa **deve** declarar as rotas de consulta atualmente sem
   prefixo. `/v1/` e `/v2/` podem ser mencionados apenas como política futura;
   não podem ser apresentados como aliases, compatibilidade já fornecida ou
   rotas montadas.
3. `README.md`, `PRD.md`, `ARCHITECTURE.md`, SPEC-0004, SPEC-0006 e o índice de
   specs **devem** usar a evidência mais recente do issue 0012: **143 passed, 36
   skipped** para a etapa offline e **36 passed, 143 deselected** para a etapa
   PostgreSQL. As evidências do issue 0008 (**127/33**, **33/127**) e do issue
   0007 (**122/33**, **33/122**) só podem ser preservadas como contexto histórico
   datado, nunca como baseline mais recente.
4. As instruções do runner **devem** dizer que a etapa offline não depende de
   flag de finalização e que o runner isola credenciais de banco antes de criar
   e injetar o alvo PostgreSQL 16 descartável na etapa própria.
5. A documentação **deve** distinguir evidência local descartável de
   verificação de Redis, DigiSac, Groq, réplicas ou produção. Não pode inferir
   disponibilidade, rollout ou garantia externa a partir dos testes locais.
6. Esta especificação é o contrato canônico para essa reconciliação; os
   contratos de webhook, finalização e verificação devem ser referenciados,
   sem duplicar suas regras funcionais.

## Validação e aceitação

O trabalho de documentação deve executar buscas direcionadas nos documentos
afetados para confirmar a ausência de alegações ativas de fluxo legado, flag de
finalização do runner e rota de consulta versionada. Deve também verificar os
links e versões em `specs/README.md` e confirmar que as contagens mais recentes
coincidem com a execução do issue 0012.

- [x] README não descreve o status de conversa como fluxo legado nem diz que o
  runner habilita modo persistente por flag.
- [x] PRD, arquitetura, README, SPEC-0004, SPEC-0006 e o índice registram a
  evidência mais recente **143/36** offline e **36/143** PostgreSQL; as
  contagens **127/33**, **33/127**, **122/33** e **33/122** estão identificadas
  como evidências históricas datadas.
- [x] SPEC-0002 e toda documentação de API ativa descrevem consultas sem
  prefixo; `/v1/` e `/v2/` permanecem somente política futura.
- [x] O diff é documental, preserva a distinção entre evidência local e
  produção e não altera contratos de código.

## Notas de implementação

Em 2026-08-14, README, PRD, ARCHITECTURE, SPEC-0002, SPEC-0004, SPEC-0006,
este índice e o plano foram conferidos contra o código montado, o runner e as
resoluções dos issues 0006–0009 e 0012. A etapa offline e a etapa PostgreSQL descartável permanecem
separadas; Redis, DigiSac, Groq, réplicas, deployment e produção continuam
fora da evidência local. Nenhum código, teste, migration, configuração, rota,
dado ou ambiente externo foi alterado.

## Decisão aberta

Uma aceitação operacional ou de produção continua bloqueada por alvo,
credenciais, autoridade de rollout, dados de teste, rollback e limiar de
aceitação não definidos. Isso bloqueia somente uma futura especificação de
operação/produção; não bloqueia esta reconciliação documental.
