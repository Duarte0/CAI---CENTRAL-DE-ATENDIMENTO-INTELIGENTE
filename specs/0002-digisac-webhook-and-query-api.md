# SPEC-0002 — Webhook DigiSac e API de consulta

- **Status:** baseline ativo, derivado da implementação; uso interno com HMAC de produção, consultas sem versão e runtime PostgreSQL-only
- **Versão:** 2.0
- **Prioridade/Fase:** P0 / baseline de requisitos
- **Rastreabilidade:** PRD §§5.1–5.2, 7 e 8; ARCHITECTURE §§3–4 e 10; `IMPLEMENTATION_PLAN.md` baseline concluído e trabalho pendente; SPEC-0001; issues 0049–0055
- **Dependências:** SPEC-0001

## Objetivo e não objetivos

Especificar a entrada autenticada de eventos DigiSac, a normalização segura e a superfície operacional de consulta. O sistema tem um único operador interno: consultas não exigem login, API key, JWT ou camada de autorização, e não há rate limiting. A integração Acessórias é aprovada, mas este contrato não adiciona sua superfície; seus requisitos de controle de acesso serão definidos antes da implementação. As consultas estão sem prefixo de versão no código atualmente montado; uma política futura pode introduzir `/v1/` e, depois, `/v2/`, mas esses prefixos não são comportamento implementado neste checkout.

## Estado de referência

As rotas montadas são `POST /webhook/digisac`, `GET /health`, `GET /queues`, e as consultas de conversa/ciclo sem prefixo de versão. Em produção, `WEBHOOK_SECRET` deve estar configurado para validar a origem DigiSac e impedir injeção de eventos; não há autenticação adicional de leitores.

**Nota estrutural (2026-08-20):** o issue 0030 isolou a persistência da chave
de evento, do histórico cronológico de atribuições e da projeção de nomes em
`src/core/ticket_assignment_repository.py`. `src/core/db.py` continua dono do
pool, da verificação do schema e da fachada de compatibilidade; a captura do
webhook, a construção de timestamp/chave e os formatos públicos permanecem
inalterados.

**Hydration fora da idempotência de evento (2026-09-03):** o issue 0051 mantém
a solicitação de hydration de `message.created`/`message.updated` como efeito
durável e separado da chave de evento. Referências repetidas podem produzir
somente a decisão observável correspondente; uma falha com `next_attempt_at`
futuro permanece agendada e não é movida para o presente. A resposta HTTP e a
deduplicação do webhook não são usadas para limpar o backoff.

**Ledger PostgreSQL de idempotência (2026-09-03):** o issue 0053 preserva a
derivação SHA-256 e a janela de uma hora, mas a decisão genérica de aceite agora
é feita atomicamente por `webhook_event_keys`. A repetição dentro da janela retorna
`202` com `status: duplicate`; uma linha expirada só pode ser substituída por um
vencedor. A decisão ocorre depois da reserva durável de áudio/imagem e antes do
`202` de aceite. Falha de PostgreSQL não é convertida em aceite; a rota falha
fechadamente. HMAC e rejeições de envelope continuam anteriores à normalização e
à inserção do ledger.

**Runtime PostgreSQL-only (2026-09-03):** o issue 0055 removeu do webhook
qualquer dependência, injeção ou publicação Redis. Reservas de mídia e a
decisão de idempotência são operações PostgreSQL; `GET /health` verifica apenas
o banco, e `GET /queues` devolve somente métricas duráveis. A mudança dos campos
de listas legadas é explícita no contrato OpenAPI e não fabrica zeros. Redis só
é acessível por comandos históricos do profile `maintenance`, com
`MAINTENANCE_REDIS_URL` explícita.

## Ingestão, validação e normalização

1. `POST /webhook/digisac` **deve** aceitar `ticket.created`, `ticket.updated`, `message.created` e `message.updated`. Evento não suportado **deve** retornar `200` com motivo seguro e não pode alterar estado.
2. JSON inválido, payload não objeto ou `data` não objeto **deve** retornar `400`. Com `WEBHOOK_SECRET` configurado, a assinatura HMAC-SHA256 de `X-Digisac-Signature` sobre o corpo bruto **deve** ser validada antes da normalização; hex puro e `sha256=<hex>` são aceitos e assinatura ausente/inválida retorna `401`.
3. O adaptador **deve** aceitar `chat`, `document`, `ptt`, `audio`, `voice` e `image`. Documento com MIME `image/*` **deve** tornar-se imagem internamente; documento não-imagem continua documento. Só `id`, nome, nome público, extensão e MIME seguros podem ser normalizados para trabalho posterior.
4. Bot, `isFromMe`, tipo não suportado, chat vazio e mensagem sem ticket **devem** ser ignorados antes de reserva de mídia e idempotência. A resposta **deve** ser `200` com motivo seguro e não pode enfileirar ou criar trabalho de ciclo.
5. `ticket.updated` **deve** capturar atribuições observadas idempotentemente. Fechamento sem protocolo válido **deve** ser ignorado com `200`; com protocolo, deve persistir um ciclo elegível para polling PostgreSQL. Abertura/reabertura cria ou recupera ciclo e não publica classificação na reabertura.

## Idempotência, mídia e respostas

1. Reserva PostgreSQL de áudio/imagem **deve** ocorrer antes da decisão de idempotência no ledger PostgreSQL. O webhook não publica trabalho ativo nem depende de Redis; falha ou ausência de Redis não pode apagar a reserva durável de mídia.
2. Repetição **não pode** duplicar ciclo ou reserva; ciclo e reserva persistentes são a deduplicação de trabalho relevante. O webhook não publica `ia_queue`.
3. A referência `message.contactId` **não pode** antecipar o retry de hydration:
   `pending`, `running`, falha futura, falha devida e hydration sucedida são
   decisões PostgreSQL separadas da idempotência do evento. O poller reclama
   falhas devidas e leases expirados; a rota não força um retry imediato.
4. A rota de produção normalmente retorna `202`; eventos seguramente ignorados retornam `200`.
5. `GET /conversations/{conversation_id}/status`, `/result`, `/cycles` e `GET /cycles/{cycle_id}/status`, `/result` **devem** retornar o estado persistente por histórico. Conversa, ciclo ou resultado ausente **deve** retornar `404`.

## Segurança, observabilidade, testes e aceitação

Logs e respostas operacionais comuns **devem** expor IDs, evento e motivo sanitizado, nunca corpo bruto, valores extraídos de mensagem/contato, segredo, token ou URL assinada. O diagnóstico de extração do parser fica limitado a presença, tipo e caminho de origem. Não há autorização de leitura ou rate limit adicional para o operador interno; a integração Acessórias aprovada exigirá revisão de acesso em seu próprio contrato.

Testes devem cobrir HMAC antes do parse, envelope/data inválidos, eventos ignorados, bots, documento-imagem versus PDF, reserva antes da idempotência PostgreSQL, concorrência e expiração do ledger, ausência de publicação Redis para áudio/imagem, fechamento/reabertura e `404` nas consultas. Também devem provar que API/webhook/health/queues iniciam sem Redis, que `/queues` contém somente métricas duráveis e que os seis campos legados não são fabricados. Testes de hydration devem provar que duas entregas ou muitas referências do mesmo sender durante um backoff não antecipam a chamada Contacts. Testes de rota devem provar que as superfícies de diagnóstico removidas não são servidas, que a rota de produção não registra corpo bruto, que uma assinatura inválida não atinge o parse e que uma falha do banco não produz aceite bem-sucedido.

- Repetição do webhook não produz fila, reserva ou ciclo duplicado.
- PDF `document` não entra na fila visual; `document` MIME `image/*` entra uma única vez.
- Com segredo configurado, uma assinatura inválida não atinge normalização nem filas.

## Decisões registradas

O sistema é interno e de operador único: não há login, API key, JWT, autorização de consultas ou rate limiting. Em produção, `WEBHOOK_SECRET` é obrigatório para validar webhooks DigiSac e impedir injeção de eventos. As consultas montadas são atualmente sem versão; a política de introduzir `/v1/` e manter compatibilidade antes de uma futura `/v2/` ainda não foi implementada. A integração Acessórias aprovada terá revisão de acesso em contrato próprio e não altera esta superfície. A remoção das superfícies de diagnóstico foi aprovada; o operador monitora o sistema via logs e nenhuma superfície de debug é necessária. Issue `0025` registra que a extração normal do webhook também emite somente metadados seguros, sem valores de entrada. Issue `0051` registra que as decisões de hydration são logadas somente com o ID opaco e a decisão sanitizada; a idempotência do evento não substitui o backoff persistido. Issue `0053` registra que o ledger genérico também expõe somente outcomes agregados, sem digest ou payload, e que `processed:*` só deixa de ser fonte de decisão após o handoff coordenado.
