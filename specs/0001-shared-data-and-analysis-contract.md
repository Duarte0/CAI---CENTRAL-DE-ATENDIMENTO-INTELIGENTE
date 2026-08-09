# SPEC-0001 — Contrato compartilhado de dados e análise

- **Status:** baseline ativo, derivado da implementação; decisões de produto registradas abaixo
- **Versão:** 1.1
- **Prioridade/Fase:** P0 / baseline de requisitos
- **Rastreabilidade:** PRD §§3, 6 e 8; ARCHITECTURE §§8–9 e 12; `IMPLEMENTATION_PLAN.md` itens 2, 4, 6 e 7; Alembic `0001_initial`–`0014_durable_retry_scheduling`
- **Dependências:** nenhuma

## Objetivo e não objetivos

Definir os contratos transversais de dados, classificação e segurança que tornam uma análise auditável, idempotente e recuperável. PostgreSQL é a fonte durável; Redis é somente transporte e coordenação transitória.

Esta especificação registra retenção indefinida, sem exclusão ou arquivamento automático. Exclusão manual direta no PostgreSQL pode ocorrer caso a caso. Não há mudanças de schema de retenção, jobs de limpeza ou automação orientada pela LGPD planejados.

## Estado de referência

`PRD.md` e `ARCHITECTURE.md` são baselines presentes, derivados da implementação. Código, migrations e configuração determinam o comportamento atual; esta especificação consolida o contrato que o trabalho pendente deve preservar. O schema é Alembic-owned até `0014_retry_scheduling`; inicialização da aplicação deve apenas verificar o schema e não pode criar nem mutar tabelas.

## Contrato de dados e integridade

1. PostgreSQL **deve** persistir classificações, vínculos ordenados de mensagens, estados/resultados de mídia, histórico de atribuição, diretório DigiSac e ciclos persistentes. Redis **deve** limitar-se a filas, locks, buffers legados, debounce, idempotência temporária e status/resultados com TTL.
2. Cada classificação **deve** ter identidade interna e `public_id` UUIDv7 único. Quando `idempotency_key` for fornecida, ela **deve** ser única e não vazia; tentativas concorrentes com a mesma chave **devem** devolver a mesma classificação sem duplicar linhas ou vínculos.
3. `classification_messages` e `conversation_cycle_messages` **devem** preservar a ordem que fundamenta o resultado. Um vínculo de mensagem e sua posição **devem** ser únicos dentro da classificação ou ciclo correspondente. Uma mensagem **não pode** pertencer a dois ciclos persistentes.
4. Timestamps duráveis **devem** usar `TIMESTAMPTZ`; listas e snapshots estruturados **devem** usar JSONB onde o schema o define. Identificadores externos não resolvidos **devem** permanecer preservados, sem nomes ou transferências inventados.
5. Alterações de schema **devem** ser Alembic aditivas e compatíveis. Backfills **devem** oferecer auditoria/dry-run, aplicação repetível e rollback em erro. Um downgrade que possa apagar classificações, ciclos ou agendamentos duráveis **deve** recusar-se explicitamente antes de perder dados.

## Contrato de IA, contexto e privacidade

1. A resposta aceita do modelo **deve conter exatamente** `intent_type`, `confidence`, `title` e `description`. Resposta ausente, incompleta ou truncada **não pode** ser persistida como classificação válida. Um `intent_type` textual completo fora da taxonomia pode ser normalizado para `other`; saída estruturalmente inválida não pode ser aceita por essa normalização.
2. `department`, `agent`, `protocol`, `display_title`, IDs, contagens e metadados de ciclo **devem** ser construídos pela aplicação, nunca solicitados como campos do modelo. `protocol` **não pode** entrar no contexto do modelo. Existindo protocolo, `display_title` **deve** ser `[{protocol}] - {title}` sem alterar `title`.
3. O histórico de atribuição **deve** ser cronológico e idempotente por chave de evento. Todas as transferências observadas **devem** ser preservadas para acompanhar os departamentos que trataram o ticket da abertura ao fechamento. A resolução de nomes **deve** usar somente o diretório DigiSac sincronizado quando disponível.
4. Buffers, contexto, snapshots, logs e registros operacionais duráveis **não podem** conter URL assinada/de download, token, segredo, corpo bruto do webhook ou binário de mídia. Podem conter metadados seguros e texto extraído.

## Observabilidade e compatibilidade

- Logs **devem** conter motivo sanitizado e IDs seguros suficientes para correlacionar falhas, sem conteúdo sensível.
- `GET /health` **deve** verificar Redis e PostgreSQL e retornar `503` quando o banco não estiver pronto.
- Os dados **devem** ser retidos indefinidamente por seu valor histórico, analítico e futuro uso na construção de FAQ sobre o corpus de classificações. Não são planejados migração de retenção, job de limpeza ou automação LGPD; exclusão manual direta no PostgreSQL é permitida caso a caso.

## Testes e aceitação

Testes de evolução PostgreSQL e de identificadores **devem** cobrir UUIDv7, unicidade, idempotência concorrente, ordem/constraints de vínculos e agenda durável. Testes do worker **devem** cobrir contrato IA, taxonomia, título/protocolo e rejeição de saída incompleta. Migrations/backfills **devem** ser verificados contra banco descartável.

- A mesma chave concorrente produz uma única classificação e o mesmo `public_id` para todos os chamadores.
- Nenhuma saída sem os quatro campos contratuais produz um resultado persistido válido.
- Uma evolução de schema prova preservação de dados ou recusa explícita antes de uma operação destrutiva.

## Decisões registradas

Retenção, privacidade e interpretação do histórico de atribuição estão decididas: os dados permanecem indefinidamente, e todas as transferências observadas são preservadas cronologicamente. O histórico será usado em futura integração com a plataforma Acessórias para registrar o caminho de roteamento departamental de cada ticket. Não há filtragem por relevância de negócio.
