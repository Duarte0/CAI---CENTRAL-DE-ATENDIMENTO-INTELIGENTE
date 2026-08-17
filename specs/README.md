# Implementation specifications

Baseline de especificações revisada no passe de specs de 2026-08-14. PRD e arquitetura estão presentes como baselines derivados da implementação; código, migrations, configuração e testes continuam prevalecendo para o comportamento atual. As especificações são contratos vinculantes para trabalho futuro e não aprovam políticas de produto ainda abertas.

## Especificações ativas

| ID | Especificação | Status | Prioridade/Fase | Dependências | Resumo |
| --- | --- | --- | --- | --- | --- |
| SPEC-0001 | [Contrato compartilhado de dados e análise](0001-shared-data-and-analysis-contract.md) | Baseline ativo v1.3 | P0 / baseline | — | Fonte durável, integridade, contrato IA, migrações e fronteiras de privacidade. |
| SPEC-0002 | [Webhook DigiSac e API de consulta](0002-digisac-webhook-and-query-api.md) | Baseline ativo v1.5 | P0 / baseline | SPEC-0001 | HMAC, normalização, eventos e consultas atualmente sem versão; não há superfície de diagnóstico de webhook. |
| SPEC-0003 | [Finalização durável, contexto e mídia](0003-durable-finalization-and-media.md) | Baseline ativo v1.3 | P0/P1 | SPEC-0001, SPEC-0002 | Ciclo persistente único, contexto, mídia, retry e recuperação concorrente. |
| SPEC-0004 | [Baseline reprodutível de testes e verificação](0004-reproducible-verification-baseline.md) | Implementado v1.6 | P0/P1 | SPEC-0001–0003 | Suíte rastreada, isolamento, runner descartável e evidência local separada por etapa. |
| SPEC-0005 | [Reconciliação do baseline documental](0005-documentation-baseline-reconciliation.md) | Implementado v1.3 | P1 / reconciliação documental | SPEC-0002–0004 | Corrige a documentação ativa sobre fluxo persistente único, rotas sem versão, OpenAPI publicado e a evidência de verificação registrada. |
| SPEC-0006 | [Documentação da API HTTP e contrato OpenAPI](0006-api-documentation-and-openapi-contract.md) | Implementado v1.1 | P1 / documentação de compatibilidade | SPEC-0001–0005 | Publica OpenAPI/Swagger/ReDoc e introdução para consumidores a partir das oito rotas HTTP atualmente montadas. |
| SPEC-0007 | [Fundação do diretório externo Acessórias](0007-acessorias-external-directory-foundation.md) | Implementado localmente v1.1; issue 0012 | P0 / Milestone A | SPEC-0001, SPEC-0004, configuração segura de credencial | Diretório PostgreSQL de empresas, contatos, departamentos e relações, com reconciliação completa paginada, retry e segurança; não cria Request nem identidade DigiSac. |
| SPEC-0008 | [Fundação de identidade de contato DigiSac](0008-digisac-contact-identity-foundation.md) | Implementado localmente v1.3; issues 0013 e 0014 | P0 / Milestone B | SPEC-0001, SPEC-0002, SPEC-0004, SPEC-0007 | Contato mínimo por `contact.id`, upsert de ticket, hydration individual e full backfill idempotentes; sem resolução de empresa. |
| SPEC-0009 | [Resolução de identidade DigiSac–Acessórias](0009-digisac-acessorias-identity-resolution.md) | Implementado localmente v1.1; issue 0015 | P1 / Milestone C | SPEC-0001, SPEC-0004, SPEC-0007, SPEC-0008 | Evidência e candidatos conservadores, vínculos muitos-para-muitos e resolução por ciclo; confirmação é manual, nunca automática. |
| SPEC-0010 | [Mapeamento de departamento DigiSac para Acessórias](0010-digisac-acessorias-department-mapping.md) | Implementado localmente v1.1; issue 0016 | P1 / Milestone D | SPEC-0001, SPEC-0003, SPEC-0007–0009 | Regras globais por IDs externos estáveis, auditoria de lifecycle e snapshots de avaliação contra `company_departments`; não usa IA nem cria Request. |
| SPEC-0011 | [Criação durável de Request Acessórias](0011-durable-acessorias-request-creation.md) | Implementado localmente v1.1; issues 0017–0018 | P1 / Milestone E | SPEC-0001, SPEC-0003, SPEC-0007–0010 | Criação multipart externa (`tipo=E`) durável, sem idempotency key do provider, com retry conservador somente sob prova de pré-envio e reconciliação manual. |

A evidência mais recente registrada para SPEC-0004 é **193 passed, 61 skipped**
na etapa offline e **61 passed, 193 deselected** na etapa PostgreSQL
descartável (issue 0024). A evidência anterior de issue 0018 foi **192 passed,
61 skipped** e **61 passed, 192 deselected**. Os resultados **183/60** e
**60/183** (issue 0017),
**177/56** e **56/177** (issue 0016),
**175/48** e **48/175** (issue 0015),
**169/42** e **42/169** (issue 0014),
**160/40** e **40/160** (issue 0013),
**151/36** e **36/151** são evidência
histórica de issue 0011; **146/36** e **36/146** são evidência
histórica de issue 0010; **143/36** e **36/143** são evidência histórica de
issue 0012; **122/33** e **33/122** são evidência histórica de
issue 0007. Esses
resultados locais não comprovam Redis, DigiSac, Groq, réplicas, deployment ou
produção.

## Próximas especificações e bloqueios

SPEC-0007 é a especificação canônica do **Milestone A — External Directory
Foundation** e foi implementada localmente pelo issue 0012. Ela registra a evidência
autorizada de base, Bearer via configuração segura, endpoints/payloads de
Departments e Companies, paginação `Pagina=N` e limite de 100 requisições por
minuto; tokens de exploração comprometidos não podem ser registrados. A
implementação deve aplicar as salvaguardas de reconciliação completa da SPEC,
sem inferir nomes de campos/parâmetros além dos observados.

SPEC-0008–SPEC-0011 são os contratos canônicos dependentes para **DigiSac
Contact Identity Foundation**, **DigiSac–Acessórias Identity Resolution**,
**Department Mapping** e **Durable Request Creation**. SPEC-0008 e seu slice de
persistência/upsert de ticket/hydration individual foram implementados pelo
issue 0013. O issue 0014 implementa o full backfill: `perPage` alto pode
concluir uma página no tenant atual, e `page=N` com `currentPage`/`lastPage`
mantém o fallback paginado, deduplicado por `contact.id` e seguro contra não
avanço. SPEC-0010 v1.1 tem sua governança inicial aprovada e foi implementada
pelo issue 0016: o mapping global por IDs externos estáveis é administrado por
procedimento `manual_db`, sem UI/endpoint e com ator opcional; regras e
avaliações de ciclo são persistidas somente no PostgreSQL. SPEC-0011 v1.1 tem
contrato Request e políticas de duplicidade, retry e reconciliação implementados
pelos issues 0017–0018; a migration `0019` e a operação durável preservam a
classificação e não inventam idempotency key do provider. Erros de conexão,
timeout ou protocolo sem marcador explícito de pré-envio ficam em
`reconciliation_required` e não podem iniciar outro POST. SPEC-0009 v1.1 foi
implementada pelo issue 0015: a variante
móvel brasileira permanece evidência conservadora, a confirmação inicial é o
procedimento controlado `manual_db`, e os resultados locais são persistidos
somente no PostgreSQL.
Milestone F continua fora do conjunto: exige decisão de produto após a criação
de Request ser comprovada. Não alterar as SPEC-0001–0006 concluídas para
atribuir retroativamente esses comportamentos; elas continuam descrevendo
somente os contratos já estabelecidos.

## Arquivos não ativos

Não há especificações superseded, deprecated ou template neste conjunto. Um contrato que vier a ser substituído deve ser preservado com status apropriado, apontar ao sucessor canônico e sair da tabela de ativos sem apagar seu histórico.

## Fluxo

1. Planejamento referencia a especificação aplicável e registra dependências/decisões abertas.
2. A passagem de issues decompõe somente especificações prontas, sem redefinir seus contratos.
3. Build implementa issues aprovadas, executa a verificação exigida e atualiza o status da especificação com evidência. O isolamento da suíte (issue 0001), o runner descartável (0002), a verificação operacional (0004), a remoção das superfícies de diagnóstico (0006), a reconciliação documental (0007 e 0009), a publicação de SPEC-0006 (0008), o Milestone A de SPEC-0007 (0012), o Milestone B de SPEC-0008 (0013 e 0014), o Milestone C de SPEC-0009 (0015) e o Milestone D de SPEC-0010 (0016) estão implementados.
