# Implementation specifications

Baseline de especificações revisada no passe de specs de 2026-08-14. PRD e arquitetura estão presentes como baselines derivados da implementação; código, migrations, configuração e testes continuam prevalecendo para o comportamento atual. As especificações são contratos vinculantes para trabalho futuro e não aprovam políticas de produto ainda abertas.

## Especificações ativas

| ID | Especificação | Status | Prioridade/Fase | Dependências | Resumo |
| --- | --- | --- | --- | --- | --- |
| SPEC-0001 | [Contrato compartilhado de dados e análise](0001-shared-data-and-analysis-contract.md) | Baseline ativo v1.1 | P0 / baseline | — | Fonte durável, integridade, contrato IA, migrações e fronteiras de privacidade. |
| SPEC-0002 | [Webhook DigiSac e API de consulta](0002-digisac-webhook-and-query-api.md) | Baseline ativo v1.5 | P0 / baseline | SPEC-0001 | HMAC, normalização, eventos e consultas atualmente sem versão; não há superfície de diagnóstico de webhook. |
| SPEC-0003 | [Finalização durável, contexto e mídia](0003-durable-finalization-and-media.md) | Baseline ativo v1.3 | P0/P1 | SPEC-0001, SPEC-0002 | Ciclo persistente único, contexto, mídia, retry e recuperação concorrente. |
| SPEC-0004 | [Baseline reprodutível de testes e verificação](0004-reproducible-verification-baseline.md) | Implementado v1.5 | P0/P1 | SPEC-0001–0003 | Suíte rastreada, isolamento, runner descartável e evidência local separada por etapa. |
| SPEC-0005 | [Reconciliação do baseline documental](0005-documentation-baseline-reconciliation.md) | Implementado v1.2 | P1 / reconciliação documental | SPEC-0002–0004 | Corrige a documentação ativa sobre fluxo persistente único, rotas sem versão e evidência de verificação registrada. |
| SPEC-0006 | [Documentação da API HTTP e contrato OpenAPI](0006-api-documentation-and-openapi-contract.md) | Implementado v1.1 | P1 / documentação de compatibilidade | SPEC-0001–0005 | Publica OpenAPI/Swagger/ReDoc e introdução para consumidores a partir das oito rotas HTTP atualmente montadas. |
| SPEC-0007 | [Fundação do diretório externo Acessórias](0007-acessorias-external-directory-foundation.md) | Implementado localmente v1.1; issue 0012 | P0 / Milestone A | SPEC-0001, SPEC-0004, configuração segura de credencial | Diretório PostgreSQL de empresas, contatos, departamentos e relações, com reconciliação completa paginada, retry e segurança; não cria Request nem identidade DigiSac. |
| SPEC-0008 | [Fundação de identidade de contato DigiSac](0008-digisac-contact-identity-foundation.md) | Ativo v1.0; bloqueado por evidência Contacts e SPEC-0007 | P0 / Milestone B | SPEC-0001, SPEC-0002, SPEC-0004, SPEC-0007 | Contato mínimo por `contact.id`, backfill/hidratação idempotentes e privacidade; não faz resolução de empresa. |
| SPEC-0009 | [Resolução de identidade DigiSac–Acessórias](0009-digisac-acessorias-identity-resolution.md) | Ativo v1.0; bloqueado por SPEC-0007–0008 e variante móvel | P1 / Milestone C | SPEC-0001, SPEC-0004, SPEC-0007, SPEC-0008 | Evidência, vínculos muitos-para-muitos e resolução por ciclo; confirmação é manual, nunca automática. |
| SPEC-0010 | [Mapeamento de departamento DigiSac para Acessórias](0010-digisac-acessorias-department-mapping.md) | Ativo v1.0; bloqueado por SPEC-0007–0009 e governança | P1 / Milestone D | SPEC-0001, SPEC-0003, SPEC-0007–0009 | Configuração auditável do departamento atual sem usar IA; não cria Request. |
| SPEC-0011 | [Criação durável de Request Acessórias](0011-durable-acessorias-request-creation.md) | Ativo v1.0; bloqueado por SPEC-0007–0010 e contrato Request | P1 / Milestone E | SPEC-0001, SPEC-0003, SPEC-0007–0010 | Efeito externo idempotente e reconciliável, sem alterar classificações ou cobrir ciclo de vida. |

A evidência mais recente registrada para SPEC-0004 é **143 passed, 36 skipped**
na etapa offline e **36 passed, 143 deselected** na etapa PostgreSQL
descartável. Os resultados **122/33** e **33/122** são evidência histórica de
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
**Department Mapping** e **Durable Request Creation**. Suas issues continuam
bloqueadas pelas evidências e decisões abertas enumeradas em cada contrato.
Milestone F continua fora do conjunto: exige decisão de produto após a criação
de Request ser comprovada. Não alterar as SPEC-0001–0006 concluídas para
atribuir retroativamente esses comportamentos; elas continuam descrevendo
somente os contratos já estabelecidos.

## Arquivos não ativos

Não há especificações superseded, deprecated ou template neste conjunto. Um contrato que vier a ser substituído deve ser preservado com status apropriado, apontar ao sucessor canônico e sair da tabela de ativos sem apagar seu histórico.

## Fluxo

1. Planejamento referencia a especificação aplicável e registra dependências/decisões abertas.
2. A passagem de issues decompõe somente especificações prontas, sem redefinir seus contratos.
3. Build implementa issues aprovadas, executa a verificação exigida e atualiza o status da especificação com evidência. Os itens 1 e 2, a verificação operacional do item 4 de SPEC-0004, a remoção das superfícies de diagnóstico de SPEC-0002, a reconciliação documental de SPEC-0005, a publicação de SPEC-0006 e o Milestone A do SPEC-0007 estão implementados.
