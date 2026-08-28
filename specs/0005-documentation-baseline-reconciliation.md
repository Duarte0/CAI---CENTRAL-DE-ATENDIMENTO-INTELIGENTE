# SPEC-0005 — Reconciliação do baseline documental

- **Status:** implementado em 2026-08-21 pelo issue 0041; evidências anteriores preservadas como históricas
- **Versão:** 1.4
- **Prioridade/Fase:** P1 / reconciliação documental
- **Rastreabilidade:** PRD §§5.4, 7, 9 e 11; ARCHITECTURE §§3, 10 e 13;
  `IMPLEMENTATION_PLAN.md`; SPEC-0004, SPEC-0006 e SPEC-0012; issues
  0006–0009, 0012 e 0038–0040 (evidência registrada)
- **Dependências:** SPEC-0002, SPEC-0003, SPEC-0004, SPEC-0006 e SPEC-0012

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

As divergências que motivaram a versão 1.3 foram corrigidas naquela
reconciliação:

1. O cabeçalho de SPEC-0002 foi alinhado ao contrato e às rotas montadas sem
   versão.
2. SPEC-0004, PRD, ARCHITECTURE, README e o índice registram a evidência mais
   recente do issue 0012: **143 passed, 36 skipped** e **36 passed, 143
   deselected**. A evidência do issue 0008 (**127/33** e **33/127**) e a do
   issue 0007 (**122/33** e **33/122**) permanecem como histórico datado.
3. README não apresenta mais o status da conversa como fluxo legado nem afirma
   que o runner ativa explicitamente um modo persistente.

### Delta v1.4 — reconciliação concluída

O código, migrations e testes agora incluem a administração autenticada de
identidade da SPEC-0012, inclusive confirmação/rejeição e redescoberta, e o
Alembic head é `0022_identity_discovery_command`. A evidência local mais recente
é a do issue 0040 em 2026-08-21: compileall e Pyright estrito passaram; **238
passed, 76 skipped** offline; e **76 passed, 238 deselected** no PostgreSQL
descartável. `PRD.md` §9 e sua rastreabilidade, `ARCHITECTURE.md` §13, README,
este índice e SPEC-0004/SPEC-0006 agora apresentam o baseline `0022`/`238+76`.
Oito operações HTTP originais permanecem distintas das seis operações internas
autenticadas da SPEC-0012. SPEC-0013 continua proposta não ativa, sem UI
montada.

## Requisitos documentais e de compatibilidade

1. A documentação ativa **deve** declarar que a finalização persistente por
   histórico DigiSac é o único caminho suportado. Nenhuma rota, status,
   configuração, fila ou instrução operacional pode apresentar fluxo legado
   como comportamento atual.
2. A documentação ativa **deve** declarar as rotas de consulta atualmente sem
   prefixo. `/v1/` e `/v2/` podem ser mencionados apenas como política futura;
   não podem ser apresentados como aliases, compatibilidade já fornecida ou
   rotas montadas.
3. Na reconciliação v1.4, `README.md`, `PRD.md`, `ARCHITECTURE.md`, SPEC-0004,
   SPEC-0006 e o índice de specs **devem** usar o baseline atual do issue 0040:
   Alembic `0022_identity_discovery_command`, **238 passed, 76 skipped** offline
   e **76 passed, 238 deselected** no PostgreSQL. Evidências anteriores só podem
   ser preservadas como histórico datado, nunca como baseline mais recente.
4. A documentação de superfície HTTP **deve** distinguir as oito operações
   originais das seis rotas administrativas autenticadas da SPEC-0012, sem
   omitir o Bearer `ADMIN_API_TOKEN`, promover a administração a API pública ou
   alegar uma UI administrativa implementada.
5. As instruções do runner **devem** dizer que a etapa offline não depende de
   flag de finalização e que o runner isola credenciais de banco antes de criar
   e injetar o alvo PostgreSQL 16 descartável na etapa própria.
6. A documentação **deve** distinguir evidência local descartável de
   verificação de Redis, DigiSac, Groq, réplicas ou produção. Não pode inferir
   disponibilidade, rollout ou garantia externa a partir dos testes locais.
7. Esta especificação é o contrato canônico para essa reconciliação; os
   contratos de webhook, finalização e verificação devem ser referenciados,
   sem duplicar suas regras funcionais.

## Validação e aceitação

O trabalho de documentação deve executar buscas direcionadas nos documentos
afetados para confirmar a ausência de alegações ativas de fluxo legado, flag de
finalização do runner e rota de consulta versionada. Deve também verificar os
links e versões em `specs/README.md` e confirmar que as contagens atuais
coincidem com a execução do issue 0040.

- [x] README não descreve o status de conversa como fluxo legado nem diz que o
  runner habilita modo persistente por flag.
- [x] PRD, arquitetura, README, SPEC-0004, SPEC-0006 e o índice registram a
  evidência atual **238/76** offline e **76/238** PostgreSQL; as contagens
  anteriores, incluindo **143/36**, **127/33**, **122/33** e **203/68**, estão
  identificadas como evidências históricas datadas.
- [x] SPEC-0002 e toda documentação de API ativa descrevem consultas sem
  prefixo; `/v1/` e `/v2/` permanecem somente política futura.
- [x] O diff é documental, preserva a distinção entre evidência local e
  produção e não altera contratos de código.
- [x] O delta v1.4 atualiza PRD, arquitetura e rastreabilidade para `0022` e
  `238/76`, e descreve toda a superfície da SPEC-0012 sem alegar uma UI.

## Notas de implementação

Em 2026-08-21, README, PRD, ARCHITECTURE, SPEC-0004, SPEC-0006, este índice e
o plano foram conferidos contra as rotas montadas, OpenAPI, migrations,
`scripts/verify.py` e as resoluções dos issues 0038–0040. A execução com
`APP_TIMEZONE=UTC` passou compileall, Pyright, **238 passed, 76 skipped** offline,
Alembic `0022_identity_discovery_command` e **76 passed, 238 deselected** no
PostgreSQL descartável. A etapa offline e a etapa PostgreSQL permanecem
separadas; Redis, DigiSac, Groq, réplicas, deployment e produção continuam fora
da evidência local. Nenhum código, teste, migration, configuração, rota, dado ou
ambiente externo foi alterado.

## Decisão aberta

Uma aceitação operacional ou de produção continua bloqueada por alvo,
credenciais, autoridade de rollout, dados de teste, rollback e limiar de
aceitação não definidos. Isso bloqueia somente uma futura especificação de
operação/produção; não bloqueia esta reconciliação documental.
