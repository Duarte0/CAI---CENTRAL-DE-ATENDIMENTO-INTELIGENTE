# Implementation specifications

Baseline de especificações revisada no passe de specs de 2026-08-13. PRD e arquitetura estão presentes como baselines derivados da implementação; código, migrations, configuração e testes continuam prevalecendo para o comportamento atual. As especificações são contratos vinculantes para trabalho futuro e não aprovam políticas de produto ainda abertas.

## Especificações ativas

| ID | Especificação | Status | Prioridade/Fase | Dependências | Resumo |
| --- | --- | --- | --- | --- | --- |
| SPEC-0001 | [Contrato compartilhado de dados e análise](0001-shared-data-and-analysis-contract.md) | Baseline ativo v1.1 | P0 / baseline | — | Fonte durável, integridade, contrato IA, migrações e fronteiras de privacidade. |
| SPEC-0002 | [Webhook DigiSac e API de consulta](0002-digisac-webhook-and-query-api.md) | Baseline ativo v1.5 | P0 / baseline | SPEC-0001 | HMAC, normalização, eventos e consultas atualmente sem versão; não há superfície de diagnóstico de webhook. |
| SPEC-0003 | [Finalização durável, contexto e mídia](0003-durable-finalization-and-media.md) | Baseline ativo v1.3 | P0/P1 | SPEC-0001, SPEC-0002 | Ciclo persistente único, contexto, mídia, retry e recuperação concorrente. |
| SPEC-0004 | [Baseline reprodutível de testes e verificação](0004-reproducible-verification-baseline.md) | Itens 1–2 e verificação operacional do item 4 implementados v1.4 | P0/P1 | SPEC-0001–0003 | Suíte rastreada, isolamento, runner descartável e evidência local separada por etapa. |
| SPEC-0005 | [Reconciliação do baseline documental](0005-documentation-baseline-reconciliation.md) | Implementado v1.1 | P1 / reconciliação documental | SPEC-0002–0004 | Corrige a documentação ativa sobre fluxo persistente único, rotas sem versão e evidência de verificação registrada. |
| SPEC-0006 | [Documentação da API HTTP e contrato OpenAPI](0006-api-documentation-and-openapi-contract.md) | Pronto para issues v1.0 | P1 / documentação de compatibilidade | SPEC-0001–0005 | Define OpenAPI/Swagger/ReDoc e introdução para consumidores a partir das oito rotas HTTP atualmente montadas. |

A evidência registrada para SPEC-0004 é **122 passed, 33 skipped** na etapa
offline e **33 passed, 122 deselected** na etapa PostgreSQL descartável. Esses
resultados locais não comprovam Redis, DigiSac, Groq, réplicas, deployment ou
produção.

## Arquivos não ativos

Não há especificações superseded, deprecated ou template neste conjunto. Um contrato que vier a ser substituído deve ser preservado com status apropriado, apontar ao sucessor canônico e sair da tabela de ativos sem apagar seu histórico.

## Fluxo

1. Planejamento referencia a especificação aplicável e registra dependências/decisões abertas.
2. A passagem de issues decompõe somente especificações prontas, sem redefinir seus contratos.
3. Build implementa issues aprovadas, executa a verificação exigida e atualiza o status da especificação com evidência. Os itens 1 e 2, a verificação operacional do item 4 de SPEC-0004, a remoção das superfícies de diagnóstico de SPEC-0002 e a reconciliação documental de SPEC-0005 estão implementados. SPEC-0006 permanece pronto para a passagem de issues.
