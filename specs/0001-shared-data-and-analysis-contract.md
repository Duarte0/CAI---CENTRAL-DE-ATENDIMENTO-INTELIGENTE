# SPEC-0001 — Contrato compartilhado de dados e análise

- **Status:** baseline ativo, derivado da implementação; issues 0010, 0024 e 0025 corrigiram a paridade da taxonomia no prompt e a fronteira de privacidade dos logs; decisões de produto registradas abaixo
- **Versão:** 1.4
- **Prioridade/Fase:** P0 / baseline de requisitos
- **Rastreabilidade:** PRD §§3, 6 e 8; ARCHITECTURE §§8–9 e 12; `IMPLEMENTATION_PLAN.md` baseline concluído e trabalho pendente; Alembic `0001_initial`–`0018_department_mapping`; SPEC-0007–0010; issues 0010, 0024 e 0025
- **Dependências:** nenhuma

## Objetivo e não objetivos

Definir os contratos transversais de dados, classificação e segurança que tornam uma análise auditável, idempotente e recuperável. PostgreSQL é a fonte durável; Redis é somente transporte e coordenação transitória.

Esta especificação registra retenção indefinida, sem exclusão ou arquivamento automático. Exclusão manual direta no PostgreSQL pode ocorrer caso a caso. Não há mudanças de schema de retenção, jobs de limpeza ou automação orientada pela LGPD planejados.

## Estado de referência

`PRD.md` e `ARCHITECTURE.md` são baselines presentes, derivados da implementação. Código, migrations e configuração determinam o comportamento atual; esta especificação consolida o contrato que o trabalho pendente deve preservar. O schema é Alembic-owned até `0018_department_mapping`; inicialização da aplicação deve apenas verificar o schema e não pode criar nem mutar tabelas.

**Evidência de implementação (2026-08-14):** issue 0010 alinhou o prompt do
worker com `VALID_INTENT_TYPES`, incluindo `financial` na lista permitida e na
orientação delimitada. Os testes verificam a paridade derivada, a preservação
do resultado canônico e a normalização/rejeição já existentes; não houve
alteração de schema, persistência, API, precedência ou política do provedor.

**Correção de privacidade (2026-08-17):** issue 0024 removeu a resposta bruta e
o preview da resposta Groq dos avisos de `_parse_result()`. A recuperação
envolta e a saída inválida continuam distinguíveis por `outcome` e metadados
estruturais limitados; o parser, a validação, o retry/dead-letter e a
persistência da classificação não foram alterados.

Issue 0025 estende essa fronteira ao parser normal do webhook: o diagnóstico de
extração preserva somente presença, tipo e caminho de origem, sem registrar
valores de mensagem/contato, URLs, segredos ou corpo bruto.

## Contrato de dados e integridade

1. PostgreSQL **deve** persistir classificações, vínculos ordenados de mensagens, estados/resultados de mídia, histórico de atribuição, diretórios DigiSac e Acessórias e ciclos persistentes. Redis **deve** limitar-se a filas, locks, idempotência temporária e status/resultados com TTL.
2. Cada classificação **deve** ter identidade interna e `public_id` UUIDv7 único. Quando `idempotency_key` for fornecida, ela **deve** ser única e não vazia; tentativas concorrentes com a mesma chave **devem** devolver a mesma classificação sem duplicar linhas ou vínculos.
3. `classification_messages` e `conversation_cycle_messages` **devem** preservar a ordem que fundamenta o resultado. Um vínculo de mensagem e sua posição **devem** ser únicos dentro da classificação ou ciclo correspondente. Uma mensagem **não pode** pertencer a dois ciclos persistentes.
4. Timestamps duráveis **devem** usar `TIMESTAMPTZ`; listas e snapshots estruturados **devem** usar JSONB onde o schema o define. Identificadores externos não resolvidos **devem** permanecer preservados, sem nomes ou transferências inventados.
5. Alterações de schema **devem** ser Alembic aditivas e compatíveis. Backfills **devem** oferecer auditoria/dry-run, aplicação repetível e rollback em erro. Um downgrade que possa apagar classificações, ciclos ou agendamentos duráveis **deve** recusar-se explicitamente antes de perder dados.

## Contrato de IA, contexto e privacidade

1. A resposta aceita do modelo **deve conter exatamente** `intent_type`, `confidence`, `title` e `description`. Resposta ausente, incompleta ou truncada **não pode** ser persistida como classificação válida. Um `intent_type` textual completo fora da taxonomia pode ser normalizado para `other`; saída estruturalmente inválida não pode ser aceita por essa normalização.
2. `department`, `agent`, `protocol`, `display_title`, IDs, contagens e metadados de ciclo **devem** ser construídos pela aplicação, nunca solicitados como campos do modelo. `protocol` **não pode** entrar no contexto do modelo. Existindo protocolo, `display_title` **deve** ser `[{protocol}] - {title}` sem alterar `title`.
3. O histórico de atribuição **deve** ser cronológico e idempotente por chave de evento. Todas as transferências observadas **devem** ser preservadas para acompanhar os departamentos que trataram o ticket da abertura ao fechamento. A resolução de nomes **deve** usar somente o diretório DigiSac sincronizado quando disponível.
4. Filas, contexto, snapshots, logs e registros operacionais duráveis **não podem** conter URL assinada/de download, token, segredo, corpo bruto do webhook, resposta bruta/parcial do modelo ou binário de mídia. Podem conter metadados seguros e texto extraído.

## Observabilidade e compatibilidade

- Logs **devem** conter motivo sanitizado, categoria/outcome e IDs seguros suficientes para correlacionar falhas, sem conteúdo sensível; diagnósticos do parser podem conter somente metadados estruturais limitados.
- Os diagnósticos de extração do webhook **devem** registrar somente evento seguro,
  presença/tipo e caminho de origem; valores extraídos, corpo bruto, URLs e
  segredos não podem atravessar essa fronteira de log.
- `GET /health` **deve** verificar Redis e PostgreSQL e retornar `503` quando o banco não estiver pronto.
- Os dados **devem** ser retidos indefinidamente por seu valor histórico, analítico e futuro uso na construção de FAQ sobre o corpus de classificações. Não são planejados migração de retenção, job de limpeza ou automação LGPD; exclusão manual direta no PostgreSQL é permitida caso a caso.

## Testes e aceitação

Testes de evolução PostgreSQL e de identificadores **devem** cobrir UUIDv7, unicidade, idempotência concorrente, ordem/constraints de vínculos e agenda durável. Testes do worker **devem** cobrir contrato IA, taxonomia, título/protocolo, rejeição de saída incompleta e ausência de conteúdo sensível nos diagnósticos do parser. Migrations/backfills **devem** ser verificados contra banco descartável.

- A mesma chave concorrente produz uma única classificação e o mesmo `public_id` para todos os chamadores.
- Nenhuma saída sem os quatro campos contratuais produz um resultado persistido válido.
- Uma evolução de schema prova preservação de dados ou recusa explícita antes de uma operação destrutiva.

## Decisões registradas

Retenção, privacidade e interpretação do histórico de atribuição estão decididas: os dados permanecem indefinidamente, e todas as transferências observadas são preservadas cronologicamente. A integração Acessórias aprovada poderá usar esse histórico para contexto de mapeamento departamental; este contrato não implementa roteamento nem criação de Request. Não há filtragem por relevância de negócio.
