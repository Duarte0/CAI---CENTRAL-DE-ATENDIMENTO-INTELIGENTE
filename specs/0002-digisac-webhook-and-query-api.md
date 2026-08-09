# SPEC-0002 — Webhook DigiSac e API de consulta

- **Status:** baseline ativo, derivado da implementação; uso interno com HMAC de produção e consultas versionadas
- **Versão:** 1.2
- **Prioridade/Fase:** P0 / baseline de requisitos
- **Rastreabilidade:** PRD §§5.1–5.2, 7 e 8; ARCHITECTURE §§3–4 e 10; `IMPLEMENTATION_PLAN.md` itens 5 e 7; SPEC-0001
- **Dependências:** SPEC-0001

## Objetivo e não objetivos

Especificar a entrada autenticada de eventos DigiSac, a normalização segura e a superfície operacional de consulta. O sistema tem um único operador interno: consultas não exigem login, API key, JWT ou camada de autorização, e não há rate limiting. Quando a integração Acessórias for construída, os requisitos de controle de acesso serão revisitados. A consulta usa `/v1/`; uma mudança incompatível futura introduzirá `/v2/`, mantendo `/v1/` durante a migração.

## Estado de referência

As rotas montadas são `POST /webhook/digisac`, `POST /webhook/debug`, `GET /health`, `GET /queues`, e as consultas de conversa/ciclo versionadas em `/v1/`. O atual `POST /webhook/debug` valida a HMAC quando configurada, não escreve nem enfileira, e devolve `raw_payload`, extração e normalização ao chamador para diagnóstico interno. Em produção, `WEBHOOK_SECRET` deve estar configurado para validar a origem DigiSac e impedir injeção de eventos; não há autenticação adicional de leitores. Há também um manipulador não montado em `src/api/debug_routes.py` que imprime headers e corpo bruto; ele **não deve** ser montado, anunciado ou usado como contrato.

## Ingestão, validação e normalização

1. `POST /webhook/digisac` **deve** aceitar `ticket.created`, `ticket.updated`, `message.created` e `message.updated`. Evento não suportado **deve** retornar `200` com motivo seguro e não pode alterar estado.
2. JSON inválido, payload não objeto ou `data` não objeto **deve** retornar `400`. Com `WEBHOOK_SECRET` configurado, a assinatura HMAC-SHA256 de `X-Digisac-Signature` sobre o corpo bruto **deve** ser validada antes da normalização; hex puro e `sha256=<hex>` são aceitos e assinatura ausente/inválida retorna `401`.
3. O adaptador **deve** aceitar `chat`, `document`, `ptt`, `audio`, `voice` e `image`. Documento com MIME `image/*` **deve** tornar-se imagem internamente; documento não-imagem continua documento. Só `id`, nome, nome público, extensão e MIME seguros podem ser normalizados para trabalho posterior.
4. Bot, `isFromMe`, tipo não suportado, chat vazio e mensagem sem ticket **devem** ser ignorados antes de reserva de mídia e idempotência. A resposta **deve** ser `200` com motivo seguro e não pode enfileirar, bufferizar ou reagendar fechamento.
5. `ticket.updated` **deve** capturar atribuições observadas idempotentemente. Fechamento sem protocolo válido **deve** ser ignorado com `200`; com protocolo, deve acionar somente o modo de finalização ativo. Abertura/reabertura cria ou recupera ciclo apenas no modo persistente e não publica classificação na reabertura.

## Idempotência, mídia e respostas

1. Reserva PostgreSQL de áudio/imagem **deve** ocorrer antes da marcação transitória de idempotência e antes da publicação Redis. Falha de publicação **deve** liberar o marcador de publicação para repetição/reconciliação.
2. No modo legado, repetição **não pode** duplicar buffer ou geração de fechamento. No modo por histórico, ciclo e reserva persistentes são a deduplicação de trabalho relevante.
3. A rota de produção normalmente retorna `202`; eventos seguramente ignorados retornam `200`. `POST /webhook/debug` **deve** usar a mesma HMAC quando `WEBHOOK_SECRET` estiver configurado e apenas normalizar/devolver diagnóstico, sem escrita ou fila.
4. `GET /v1/conversations/{conversation_id}/status`, `/result`, `/cycles` e `GET /v1/cycles/{cycle_id}/status`, `/result` **devem** retornar o estado persistente por histórico. Conversa, ciclo ou resultado ausente **deve** retornar `404`.

## Segurança, observabilidade, testes e aceitação

Logs e respostas operacionais comuns **devem** expor IDs, evento e motivo sanitizado, nunca corpo bruto, segredo, token ou URL assinada. A exceção atual é a resposta do diagnóstico montado: ela devolve o `raw_payload` já recebido ao chamador interno quando a HMAC está ativa; esse comportamento **não deve** ser copiado para logs, snapshots, filas ou consultas. Não há autorização de leitura ou rate limit adicional para o operador interno; a futura integração Acessórias exigirá nova revisão de acesso.

Testes devem cobrir HMAC antes do parse, envelope/data inválidos, eventos ignorados, bots, documento-imagem versus PDF, reserva antes de idempotência, falha de publicação, fechamento/reabertura e `404` nas consultas. O diagnóstico deve provar HMAC antes do parse, ausência de escrita/publicação e o formato atual que inclui `raw_payload`; testes de logs devem provar que a rota de produção não registra corpo bruto.

- Repetição do webhook não produz fila, reserva, buffer ou ciclo duplicado.
- PDF `document` não entra na fila visual; `document` MIME `image/*` entra uma única vez.
- Com segredo configurado, uma assinatura inválida não atinge normalização nem filas.
- Uma chamada de diagnóstico autenticada não cria reserva, buffer, ciclo ou job e devolve o payload recebido somente na sua resposta de diagnóstico.

## Decisões registradas

O sistema é interno e de operador único: não há login, API key, JWT, autorização de consultas ou rate limiting. Em produção, `WEBHOOK_SECRET` é obrigatório para validar webhooks DigiSac e impedir injeção de eventos. Apenas as consultas usam `/v1/`; futuras mudanças incompatíveis usarão `/v2/` com `/v1/` funcional durante a migração. O controle de acesso será revisitado quando a integração Acessórias for construída.
