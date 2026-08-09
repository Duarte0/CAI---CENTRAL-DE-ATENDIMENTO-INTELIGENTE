# Implementation specifications

Baseline de especificações revisada no passe de specs de 2026-08-09. PRD e arquitetura estão presentes como baselines derivados da implementação; código, migrations, configuração e testes continuam prevalecendo para o comportamento atual. As especificações são contratos vinculantes para trabalho futuro e não aprovam políticas de produto ainda abertas.

## Especificações ativas

| ID | Especificação | Status | Prioridade/Fase | Dependências | Resumo |
| --- | --- | --- | --- | --- | --- |
| SPEC-0001 | [Contrato compartilhado de dados e análise](0001-shared-data-and-analysis-contract.md) | Baseline ativo v1.1 | P0 / baseline | — | Fonte durável, integridade, contrato IA, migrações e fronteiras de privacidade. |
| SPEC-0002 | [Webhook DigiSac e API de consulta](0002-digisac-webhook-and-query-api.md) | Baseline ativo v1.2 | P0 / baseline | SPEC-0001 | HMAC, normalização, eventos, idempotência, rotas e diagnóstico bruto interno sujeito a decisão de segurança. |
| SPEC-0003 | [Finalização durável, contexto e mídia](0003-durable-finalization-and-media.md) | Baseline ativo v1.2 | P0/P1 | SPEC-0001, SPEC-0002 | Ciclos, os dois modos de finalização enquanto suportados, contexto, mídia, retry e recuperação concorrente. |
| SPEC-0004 | [Baseline reprodutível de testes e verificação](0004-reproducible-verification-baseline.md) | Item 1 implementado; runner PostgreSQL pendente v1.1 | P0 / fase 0 | SPEC-0001–0003 | Suíte rastreada, isolamento, runner descartável e evidência de release. |

## Arquivos não ativos

Não há especificações superseded, deprecated ou template neste conjunto. Um contrato que vier a ser substituído deve ser preservado com status apropriado, apontar ao sucessor canônico e sair da tabela de ativos sem apagar seu histórico.

## Fluxo

1. Planejamento referencia a especificação aplicável e registra dependências/decisões abertas.
2. A passagem de issues decompõe somente especificações prontas, sem redefinir seus contratos.
3. Build implementa issues aprovadas, executa a verificação exigida e atualiza o status da especificação com evidência. O item 1 de SPEC-0004 está implementado; o runner PostgreSQL do item 2 permanece pendente.
