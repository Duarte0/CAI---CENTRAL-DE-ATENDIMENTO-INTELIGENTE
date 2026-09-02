# SPEC-0006 — Documentação da API HTTP e contrato OpenAPI

- **Status:** implementado; delta v1.2 documenta a superfície administrativa autenticada já montada, sem mudança de comportamento HTTP
- **Versão:** 1.2
- **Prioridade/Fase:** P1 / documentação de compatibilidade
- **Rastreabilidade:** PRD §§2, 5.1, 7–8 e 10; ARCHITECTURE §§3, 10 e 13;
  `IMPLEMENTATION_PLAN.md`; SPEC-0001–0005 e SPEC-0012
- **Dependências:** SPEC-0001, SPEC-0002, SPEC-0003, SPEC-0004, SPEC-0005 e
  SPEC-0012

## Objetivo e não objetivos

Definir a documentação oficial, completa e verificável da superfície HTTP já montada no CAI. A implementação resultante produz uma especificação OpenAPI 3.x válida, integrada à aplicação FastAPI, e uma introdução curta para consumidores internos, sem exigir que a issue redescubra rotas, respostas, segurança ou limites atuais.

Esta SPEC não implementa documentação, não altera handlers, regras de negócio, schemas persistidos, workers, testes, autenticação, rate limiting ou infraestrutura. Ela **must not** criar endpoints de negócio, aliases `/v1` ou `/v2`, autenticação de consulta, nem alterar respostas para torná-las mais convenientes ao Swagger. Necessidades de evolução de API identificadas abaixo são gaps separados, não parte implícita desta entrega.

## Estado de referência e superfície confirmada

`src/api/routes.py` monta as oito operações de negócio originais abaixo, sem
prefixo de versão:

| Tag futura | Método e path | Fonte de resposta atual | Finalidade |
| --- | --- | --- | --- |
| Operações | `GET /health` | `dict[str, str]` | Readiness de Redis e PostgreSQL. |
| Operações | `GET /queues` | dicionário montado pelo handler | Métricas de filas, dead letters e ciclos por status. |
| Webhook DigiSac | `POST /webhook/digisac` | dicionários variantes | Ingestão de eventos DigiSac. |
| Conversas | `GET /conversations/{conversation_id}/status` | `ConversationProcessing` | Estado do ciclo mais recente. |
| Conversas | `GET /conversations/{conversation_id}/result` | linha de resultado projetada pelo banco | Última classificação disponível. |
| Conversas | `GET /conversations/{conversation_id}/cycles` | lista de linhas de ciclo | Histórico de ciclos, com `limit`. |
| Ciclos | `GET /cycles/{cycle_id}/status` | linha de ciclo completa | Registro persistente de um ciclo. |
| Ciclos | `GET /cycles/{cycle_id}/result` | linha de resultado projetada pelo banco | Classificação de um ciclo. |

Além delas, o router separado da SPEC-0012 monta seis operações internas sob
`/admin/acessorias`: triagem de vínculos, detalhe de identidade, busca de
empresas, confirmação, rejeição e redescoberta. Elas usam Bearer
`ADMIN_API_TOKEN`, são descritas pelo OpenAPI como administrativas internas e
não mudam a ausência de autenticação nas consultas operacionais originais.

A criação atual de `FastAPI` não sobrescreve as URLs-padrão: `GET /openapi.json`, `GET /docs` (Swagger UI) e `GET /redoc` (ReDoc) já são rotas auxiliares do framework. Não são endpoints de negócio adicionais. A composição publicada em `src/api/openapi.py` preserva essas URLs, fornece servidor de desenvolvimento, esquema HMAC condicional e projeções de resposta. Mudança futura deve preservar essas garantias, salvo uma mudança de API aprovada em SPEC distinta. A ausência de `response_model` explícito na maioria dos handlers continua sendo uma limitação de enforcement, não uma lacuna do documento publicado.

O código é a fonte de verdade para este contrato. Em particular, `ConversationProcessing` é o único `response_model` atual; `GET /queues`, webhook, resultados e registros de ciclo devolvem dicionários. A implementação de documentação **must** compor modelos/esquemas de documentação que representem fielmente esses JSONs, sem mudar os corpos, códigos ou semântica dos handlers. Onde uma linha persistida é devolvida com `SELECT *`, somente os campos realmente serializados por esse endpoint podem entrar no schema público; tabelas, campos e chaves que não são devolvidos **must not** ser promovidos a contrato público.

## Formato, publicação e prevenção de drift

1. A aplicação **must** gerar OpenAPI 3.x válido a partir da aplicação FastAPI e, quando necessário, de composição próxima aos seus modelos/contratos reais; uma cópia manual desconectada de handlers e Pydantic **must not** tornar-se fonte concorrente de verdade.
2. O documento **must** conter `info`, `servers`, `tags`, `paths`, parâmetros, `requestBody`, responses, `components/schemas`, `components/securitySchemes`, `security` e exemplos sempre que aplicáveis. `info` **must** refletir o título e versão efetivos da aplicação ou uma fonte única que os alimente; não inventar versão sem relação com o runtime.
3. `servers` **must** usar apenas valores seguros e verificáveis. A página introdutória **must** identificar `http://localhost:8000` como base URL de desenvolvimento, coerente com `WEBHOOK_URL` padrão; ela **must not** declarar uma URL de produção sem configuração/autoridade correspondente.
4. Tags **must** separar `Webhook DigiSac`, `Operações`, `Conversas`, `Ciclos`
   e `Administração`. O OpenAPI **must** apresentar cada operação
   montada exatamente uma vez: as oito operações de negócio originais e as seis
   rotas administrativas internas da SPEC-0012. Ele **must not** apresentar
   `/v1/...`, `/v2/...`, diagnósticos de webhook removidos, UI administrativa ou
   rotas não montadas como implementadas.
5. Estruturas repetidas **must** usar `components/schemas` e `$ref` quando isto não ocultar uma variação real. Candidatas incluem classificação, status de processamento, resumo/registro de ciclo, resultado, métricas, respostas de webhook e erros por formato real.
6. Como o contrato de erro atual não é uniforme, a documentação **must not** inventar um `Error` universal. Ela **must** documentar separadamente o objeto `{"detail": "..."}` dos `HTTPException` conhecidos e o formato padrão FastAPI de erro de validação (`422`) quando ele ocorrer; falhas não mapeadas pelo código permanecem resposta de servidor, sem formato de negócio prometido.
7. O OpenAPI **must** usar exemplos fictícios, sanitizados e consistentes com seus schemas. Nenhum exemplo, descrição ou metadado **must not** conter segredo, token, URL assinada/de download, cabeçalho/corpo bruto de webhook ou mídia binária.

Como o repositório concentra documentação de consumidor no `README.md` e não possui uma árvore `docs/`, a seção “API HTTP” publicada deve permanecer a introdução concisa para propósito, `http://localhost:8000` em desenvolvimento, superfície suportada, HMAC do webhook, ausência atual de autenticação nas consultas internas, Bearer `ADMIN_API_TOKEN` somente na administração interna, formato geral de resultados, erros, estados de processamento, links para `/openapi.json`, `/docs` e `/redoc`, e o aviso de versionamento sem prefixo. A referência detalhada permanece no OpenAPI, Swagger UI e ReDoc; isso não autoriza uma UI administrativa.

## Contratos por endpoint

### Webhook DigiSac — `POST /webhook/digisac`

1. A operação **must** declarar `application/json` e o envelope permissivo realmente aceito: objeto de topo com `event` e `data`; o modelo atual tolera campos extras e variações, mas o handler requer `data` como objeto para processar. O schema **must** marcar como exemplos suportados, e não como exclusividade, os eventos `ticket.created`, `ticket.updated`, `message.created` e `message.updated`.
2. Os exemplos de `ticket.*` **must** demonstrar `data.id`, `isOpen`, `protocol` quando aplicável e identificadores de atribuição seguros. Os de `message.*` **must** demonstrar `data.id`, `ticketId`, `type`, `isFromMe`, texto ou metadados de arquivo seguros. Tipos atualmente tratados são `chat`, `document`, `ptt`, `audio`, `voice` e `image`; um `document` com MIME `image/*` é processado como imagem. O schema **must not** exigir ou expor URL de arquivo, credenciais ou binário.
3. Quando `WEBHOOK_SECRET` estiver configurado, `X-Digisac-Signature` **must** ser documentado como header obrigatório para esta operação, com HMAC-SHA256 do corpo bruto, aceitando digest hexadecimal puro ou `sha256=<digest>`. A dependência valida antes de parse/normalização; assinatura ausente ou inválida retorna `401` com `{"detail": "Missing webhook signature"}` ou `{"detail": "Invalid webhook signature"}`. Quando o segredo não estiver configurado, a dependência atual não exige header; a documentação **must** representar essa condicionalidade, sem publicar valor de segredo nem prometer que produção aceite chamadas sem HMAC.
4. JSON inválido, payload que não é objeto, ou `data` que não é objeto **must** documentar `400` e os detalhes atuais: `Invalid JSON payload`, `Webhook payload must be an object` e `Malformed Digisac payload: 'data' must be an object`. A validação HMAC vem antes desses erros quando habilitada.
5. O retorno normal **must** documentar `202` e as variantes reais: `received` (`conversation_id`, flags `transcription_queued` e `image_extraction_queued`), `duplicate` (`conversation_id`), `ticket_created` (`conversation_id`, `cycle_id`, `cycle_created`), `ticket_reopened` (mais `queued: false`), `ticket_updated` (`queued: false`), e `ticket_closed`/`ticket_already_closed` (`cycle_id`, `queued`).
6. O retorno `200` **must** documentar evento ignorado como `{"status":"ignored","reason":"..."}` e usar apenas razões sanitizadas observadas: `unsupported_event`, `missing_ticket_id`, `missing_protocol`, `is_from_bot`, `bot_origin_fallback`, `missing_is_from_me`, `unsupported_message_type`, `empty_message_text` e `missing_or_invalid_data`. As variações que incluem `conversation_id` **must** ser mostradas somente onde o handler a retorna. Esses eventos não criam trabalho; duplicação de mensagem retorna `202` com `status: duplicate`, e a identidade durável de ciclo/mídia evita duplicação mesmo em repetição.
7. A operação **must** declarar que o aceite assíncrono não significa classificação concluída. Reserva durável precede publicação de mídia; fechamento persiste ciclo para polling PostgreSQL, sem publicar `ia_queue`. Esses são comportamentos operacionais de contrato, não promessas de SLA.

### Operações — `GET /health` e `GET /queues`

1. `GET /health` **must** documentar sucesso `200` como `{"status":"ok"}`. Se `database_is_ready()` retornar falso, o handler retorna `503` com `{"detail":"database unavailable"}`. Redis é testado antes disso; exceção de Redis não é convertida pelo handler em um `503` com corpo estável, portanto a documentação **must not** prometer um formato/código específico para essa falha não mapeada.
2. `GET /queues` **must** documentar `200` com inteiros legados `ia_queue` e `ia_dead_letter`, listas legadas de mídia `audio_transcription_queue`, `audio_transcription_dead_letter`, `image_extraction_queue`, `image_extraction_dead_letter`, contadores PostgreSQL `ia_due`, `ia_scheduled`, `ia_leased`, `audio_due`, `audio_scheduled`, `audio_leased`, `audio_stale`, `audio_completed`, `audio_failed` e `conversation_cycles`. As listas `ia_*` e `audio_*` são visibilidade de resíduo de cutover, não backlog de trabalho ativo; os contadores duráveis são a visão operacional de finalização e transcrição. `conversation_cycles` é um mapa de contagens agrupadas por status e pode ser `{}` quando a capability de ciclos não estiver disponível; a especificação **must not** expor chaves Redis, mensagens ou detalhes de worker que a rota não devolve.
3. Ambas as operações não possuem autenticação/autorização adicional no código atual. Falhas não mapeadas de Redis/banco **must** ser descritas apenas como falhas de servidor quando necessário, sem criar contrato uniforme inexistente.

### Conversas e ciclos

1. `conversation_id` é o identificador externo textual do ticket/conversa DigiSac, não `public_id`. Os paths de ciclo recebem `cycle_id`, que na implementação é o UUID público persistido (`conversation_processing_cycles.public_id`); `classification_public_id` é outro UUID público, somente exposto em resultados. A documentação **must** distinguir esses três nomes e **must not** chamar `conversation_id` de UUID sem validação de código.
2. `GET /conversations/{conversation_id}/status` **must** referenciar o schema Pydantic atual `ConversationProcessing`: `conversation_id`, `cycle_id`, `status`, `started_at`, `completed_at`, `error_message`, `result`, `retry_count`, `transient_retry_count` e `max_retries`. O handler atualmente preenche o último ciclo e não preenche `result`. Os valores persistíveis e observáveis por esse handler são `open`, `pending`, `recovering_messages`, `waiting_media`, `media_blocked`, `building_context`, `summarizing`, `classifying`, `completed`, `completed_with_warnings`, `retryable_failure` e `failed`; exemplos **must** cobrir pelo menos processamento intermediário e um terminal. `ProcessingStatus` também declara `processing`, mas a constraint persistida de ciclos não o aceita e o handler sempre lê essa coluna: a implementação da documentação **must** documentar essa divergência sem apresentar `processing` como estado emitido. Conversa inexistente retorna `404` com `{"detail":"Conversation not found"}`.
3. `GET /conversations/{conversation_id}/result` **must** descrever o resultado projetado: `cycle_id`, `conversation_id`, `sequence_number`, `status`, `warning_count`, `classification_public_id`, `intent_type`, `confidence`, `title`, `protocol`, `description`, `department`, `agent`, `message_count` e `processed_at`. Resultado não disponível, inclusive ciclo existente ainda sem `classification_public_id`, retorna `404` com `{"detail":"Result not available"}`.
4. `GET /conversations/{conversation_id}/cycles` **must** declarar o query parameter opcional `limit: integer`, default `50`. O handler limita o resultado efetivo ao intervalo 1–100, embora a assinatura FastAPI atual não publique esses limites; a documentação **must** representar ambos o default e o clamp operacional sem alegar que a requisição inválida é rejeitada por esse limite. Erro de tipo para `limit` é a validação padrão FastAPI (`422`). Conversa sem ciclos retorna `200` com lista vazia, não `404`.
5. `GET /cycles/{cycle_id}/status` **must** documentar todos e somente os campos serializados da linha de `conversation_processing_cycles` atual: `id`, `public_id`, `conversation_id`, `sequence_number`, `protocol`, `cycle_started_at`, `ticket_closed_at`, `cycle_start_strategy`, `open_event_key`, `close_event_key`, `status`, `attempt_count`, `transient_retry_count`, `error_phase`, `error_message`, `warning_count`, `snapshot_json`, `rendered_context`, `model_context`, `context_reduction_applied`, `context_reduction_json`, `history_recovery_attempt`, `history_page_count`, `processing_time_ms`, `classification_id`, `next_attempt_at`, `enqueued_at`, `lease_owner`, `lease_expires_at`, `created_at`, `updated_at` e `completed_at`. UUIDs e timestamps **must** ter formatos OpenAPI apropriados; campos JSON permanecem objetos/listas de forma permissiva quando o código não fixa sua estrutura. Ciclo inexistente retorna `404` com `{"detail":"Cycle not found"}`.
6. `GET /cycles/{cycle_id}/result` **must** reutilizar o schema de resultado de conversa. Ciclo inexistente retorna `404` com `{"detail":"Cycle not found"}`; ciclo existente sem classificação retorna `404` com `{"detail":"Cycle result not available"}`. `completed` e `completed_with_warnings` são terminais, mas a disponibilidade é determinada pelo `classification_public_id` que o handler consulta, não somente pelo texto do status.
7. Para resultados de classificação, o schema reutilizável **must** refletir o JSON retornado pelo `LEFT JOIN`, inclusive nulabilidade de classificação antes da guarda interna. A taxonomia efetivamente persistida/produzida para `intent_type` é `question`, `problem`, `request`, `complaint`, `payment`, `billing`, `financial`, `document`, `protocol` e `other`; `department` e `agent` são listas construídas pela aplicação. Exemplos de sucesso **must** usar uma classificação concluída e dados fictícios.
8. Os quatro endpoints de resultado/status **must** explicar estados intermediários versus terminais, disponibilidade posterior do resultado e `404`; não devem inferir SLA, polling interval ou êxito de processamento a partir de `202` do webhook. Parâmetros path inválidos não possuem validação de formato declarada no handler atual; a documentação **must not** inventar `422` de UUID para eles.

## Segurança, privacidade e compatibilidade

1. O OpenAPI **must** conter um `securityScheme` de HMAC/header para o webhook
   e aplicá-lo somente de modo condicional à operação DigiSac, com explicação de
   `WEBHOOK_SECRET`. Ele **must** conter também o esquema Bearer opaco da
   administração e aplicá-lo somente às seis rotas `/admin/acessorias` da
   SPEC-0012. Ele **must not** modelar API key, Bearer/JWT, login ou autorização
   nos endpoints de consulta operacionais, pois não existem nesse contrato.
2. Documentação, UI e exemplos **must not** exibir corpos brutos de webhook, segredos, tokens, headers recebidos, URLs assinadas/de download ou mídia binária. Não existe endpoint de diagnóstico de webhook e `/webhook/debug` **must not** aparecer.
3. As rotas implementadas permanecem sem prefixo. A documentação **must** usar os paths exatamente como montados e explicar que `/v1/`/`/v2/` são apenas política futura possível, não superfície disponível. Mudança de versão, autenticação, rate limiting, schemas de resposta ou URLs de documentação requer SPEC/issue separada quando alterar comportamento.

## Testes, validação e aceitação de regressão

1. Os testes do documento gerado **must** comprovar OpenAPI 3.x válido,
   título/versão corretos, presença única das oito operações originais e das
   seis operações administrativas, e ausência de `/v1`, `/v2`, diagnósticos
   removidos e UI não montada. O teste **must** conferir métodos, tags, paths,
   parâmetros path e `limit`, responses relevantes, `$ref` reutilizáveis e
   exemplos válidos contra seus schemas.
2. Os testes **must** verificar segurança do webhook: header e explicação HMAC condicional, `401` antes de parse, ausência de segredo nos exemplos/documento e nenhuma security scheme fictícia em consultas. Também **must** verificar `400`, `200` ignorado, `202` aceito/duplicado, `404` de entidade/resultado, `422` de `limit`, `503` de banco em health e a distinção entre falha de Redis não mapeada e contrato `503` do banco.
3. Os schemas de resposta **must** ser comparados contra `ConversationProcessing`, a projeção de `get_cycle_result`, a linha exposta por `get_cycle`/`list_cycles` e o dicionário real de `queue_metrics`; mudanças em handler, modelo ou migration que alterem resposta **must** atualizar a documentação e seus testes no mesmo change.
4. Testes de UI/endpoints de documentação **must** confirmar que `/openapi.json`, `/docs` e `/redoc` respondem corretamente. A página de `README.md` **must** apontar apenas para esses paths e base URL de desenvolvimento confirmados.
5. A validação de repositório **must** usar os comandos existentes: `PYTHONPATH=/app python -m pytest -q --ignore=tests/test_webhook_local.py`, `npx --yes pyright` quando novos modelos/tipagem entrarem no escopo, e o runner completo `PYTHONPATH=/app python scripts/verify.py` quando a mudança alcançar a suíte/matriz canônica. `tests/test_webhook_local.py` permanece opt-in e não entra na automação canônica sem API local iniciada.

Critérios verificáveis de aceitação:

- OpenAPI, Swagger UI e ReDoc documentam a superfície montada sem criar ou alegar endpoints adicionais.
- Cada exemplo solicitado — webhook aceito, ignorado, inválido e assinatura inválida; health saudável e indisponível; processamento; ciclo concluído; classificação; recurso inexistente — é sanitizado e válido para seu schema.
- A documentação distingue identificadores externos, `cycle_id`, `public_id`, estados, resultado indisponível e `404` sem inventar enum, validação ou autenticação.
- Não há segredo nem artefato sensível no documento, UI ou introdução; e os testes existentes continuam passando nos comandos aplicáveis.

## Gaps e decisões abertas

Não há decisão de produto pendente para a publicação concluída. Permanecem três limitações de implementação que qualquer issue posterior **must** registrar sem resolvê-las silenciosamente: (1) respostas de ciclo, resultado, webhook e filas não têm `response_model` explícito e parte do ciclo é devolvida como `SELECT *`; qualquer redução/estabilização de campos exige mudança de API separada; (2) `ProcessingStatus` declara `processing`, mas esse valor não é permitido pelo schema persistido que alimenta a rota de status; o documento registra a divergência sem apresentar `processing` como emitido; (3) só indisponibilidade de banco em `/health` é mapeada para `503`, enquanto falhas de Redis não têm resposta de dependência estável. Essas limitações não invalidam a documentação publicada, mas bloqueiam prometer enforcement ou erro operacional mais forte que o código atual.

## Notas de implementação

Em 2026-08-13, o contrato foi publicado por uma composição gerada a partir das
rotas FastAPI em `src/api/openapi.py`, mantendo os handlers, os códigos de
resposta e a serialização existentes. O documento contém as oito operações
originais e, após os issues 0038–0040, as seis operações administrativas
internas da SPEC-0012, servidor de desenvolvimento, cinco tags, os esquemas
HMAC e Bearer condicionais, projeções sanitizadas e as URLs padrão
`/openapi.json`, `/docs` e `/redoc`. O README foi atualizado com a introdução
para consumidores internos; nenhuma UI administrativa foi adicionada.

Os testes focados do contrato e dos endpoints de documentação passaram (**5
passed**, evidência de 2026-08-13). A evidência canônica mais recente, registrada
no issue 0040 e conferida nesta reconciliação com `APP_TIMEZONE=UTC`, passou
compileall, Pyright estrito, **238 passed, 76 skipped** offline, Alembic
`0022_identity_discovery_command` e **76 passed, 238 deselected** no PostgreSQL
16 descartável. Os skips e a matriz descartável não comprovam Redis, DigiSac,
Groq, secret manager, réplicas, deployment ou produção.
