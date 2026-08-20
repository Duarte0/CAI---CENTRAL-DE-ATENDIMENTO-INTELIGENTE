# SPEC-0002 — Webhook DigiSac e API de consulta

- **Status:** baseline ativo, derivado da implementação; uso interno com HMAC de produção e consultas sem versão
- **Versão:** 1.6
- **Prioridade/Fase:** P0 / baseline de requisitos
- **Rastreabilidade:** PRD §§5.1–5.2, 7 e 8; ARCHITECTURE §§3–4 e 10; `IMPLEMENTATION_PLAN.md` baseline concluído e trabalho pendente; SPEC-0001
- **Dependências:** SPEC-0001

## Objetivo e não objetivos

Especificar a entrada autenticada de eventos DigiSac, a normalização segura e a superfície operacional de consulta. O sistema tem um único operador interno: consultas não exigem login, API key, JWT ou camada de autorização, e não há rate limiting. A integração Acessórias é aprovada, mas este contrato não adiciona sua superfície; seus requisitos de controle de acesso serão definidos antes da implementação. As consultas estão sem prefixo de versão no código atualmente montado; uma política futura pode introduzir `/v1/` e, depois, `/v2/`, mas esses prefixos não são comportamento implementado neste checkout.

## Estado de referência

As rotas montadas são `POST /webhook/digisac`, `GET /health`, `GET /queues`, e as consultas de conversa/ciclo sem prefixo de versão. Em produção, `WEBHOOK_SECRET` deve estar configurado para validar a origem DigiSac e impedir injeção de eventos; não há autenticação adicional de leitores.

## Ingestão, validação e normalização

1. `POST /webhook/digisac` **deve** aceitar `ticket.created`, `ticket.updated`, `message.created` e `message.updated`. Evento não suportado **deve** retornar `200` com motivo seguro e não pode alterar estado.
2. JSON inválido, payload não objeto ou `data` não objeto **deve** retornar `400`. Com `WEBHOOK_SECRET` configurado, a assinatura HMAC-SHA256 de `X-Digisac-Signature` sobre o corpo bruto **deve** ser validada antes da normalização; hex puro e `sha256=<hex>` são aceitos e assinatura ausente/inválida retorna `401`.
3. O adaptador **deve** aceitar `chat`, `document`, `ptt`, `audio`, `voice` e `image`. Documento com MIME `image/*` **deve** tornar-se imagem internamente; documento não-imagem continua documento. Só `id`, nome, nome público, extensão e MIME seguros podem ser normalizados para trabalho posterior.
4. Bot, `isFromMe`, tipo não suportado, chat vazio e mensagem sem ticket **devem** ser ignorados antes de reserva de mídia e idempotência. A resposta **deve** ser `200` com motivo seguro e não pode enfileirar ou criar trabalho de ciclo.
5. `ticket.updated` **deve** capturar atribuições observadas idempotentemente. Fechamento sem protocolo válido **deve** ser ignorado com `200`; com protocolo, deve persistir e publicar um ciclo persistente. Abertura/reabertura cria ou recupera ciclo e não publica classificação na reabertura.

## Idempotência, mídia e respostas

1. Reserva PostgreSQL de áudio/imagem **deve** ocorrer antes da marcação transitória de idempotência e antes da publicação Redis. Falha de publicação **deve** liberar o marcador de publicação para repetição/reconciliação.
2. Repetição **não pode** duplicar ciclo, reserva ou publicação persistente; ciclo e reserva persistentes são a deduplicação de trabalho relevante.
3. A rota de produção normalmente retorna `202`; eventos seguramente ignorados retornam `200`.
4. `GET /conversations/{conversation_id}/status`, `/result`, `/cycles` e `GET /cycles/{cycle_id}/status`, `/result` **devem** retornar o estado persistente por histórico. Conversa, ciclo ou resultado ausente **deve** retornar `404`.

## Segurança, observabilidade, testes e aceitação

Logs e respostas operacionais comuns **devem** expor IDs, evento e motivo sanitizado, nunca corpo bruto, valores extraídos de mensagem/contato, segredo, token ou URL assinada. O diagnóstico de extração do parser fica limitado a presença, tipo e caminho de origem. Não há autorização de leitura ou rate limit adicional para o operador interno; a integração Acessórias aprovada exigirá revisão de acesso em seu próprio contrato.

Testes devem cobrir HMAC antes do parse, envelope/data inválidos, eventos ignorados, bots, documento-imagem versus PDF, reserva antes de idempotência, falha de publicação, fechamento/reabertura e `404` nas consultas. Testes de rota devem provar que as superfícies de diagnóstico removidas não são servidas, que a rota de produção não registra corpo bruto e que uma assinatura inválida não atinge o parse.

- Repetição do webhook não produz fila, reserva ou ciclo duplicado.
- PDF `document` não entra na fila visual; `document` MIME `image/*` entra uma única vez.
- Com segredo configurado, uma assinatura inválida não atinge normalização nem filas.

## Decisões registradas

O sistema é interno e de operador único: não há login, API key, JWT, autorização de consultas ou rate limiting. Em produção, `WEBHOOK_SECRET` é obrigatório para validar webhooks DigiSac e impedir injeção de eventos. As consultas montadas são atualmente sem versão; a política de introduzir `/v1/` e manter compatibilidade antes de uma futura `/v2/` ainda não foi implementada. A integração Acessórias aprovada terá revisão de acesso em contrato próprio e não altera esta superfície. A remoção das superfícies de diagnóstico foi aprovada; o operador monitora o sistema via logs e nenhuma superfície de debug é necessária. Issue `0025` registra que a extração normal do webhook também emite somente metadados seguros, sem valores de entrada.
