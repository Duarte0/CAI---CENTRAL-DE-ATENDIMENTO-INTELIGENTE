# SPEC-0003 — Finalização durável, contexto e mídia

- **Status:** baseline ativo, derivado da implementação; remoção do legado aprovada para o refactor completo
- **Versão:** 1.2
- **Prioridade/Fase:** P0/P1 / operação durável e verificação
- **Rastreabilidade:** PRD §§5.3–5.4, 6 e 8; ARCHITECTURE §§4–7 e 12; `IMPLEMENTATION_PLAN.md` itens 2, 4 e 8; Alembic `0013_conversation_cycles`, `0014_durable_retry_scheduling`; SPEC-0001–0002
- **Dependências:** SPEC-0001, SPEC-0002

## Objetivo e não objetivos

Definir a finalização por histórico DigiSac e os contratos de mídia, contexto e recuperação. O buffer Redis legado, seu debounce, chaves, tratamento no worker e fixtures serão removidos no refactor completo; isso não define SLA de fornecedor, reprocessamento amplo, mudança de modelo ou resposta automática ao cliente.

## Modo persistente, ciclo e concorrência

1. Quando `DIGISAC_HISTORY_FINALIZATION_ENABLED=true`, o modo persistente é o
   caminho aprovado: abertura/reabertura **deve** criar ou recuperar um ciclo
   persistente e fechamento **deve** persistir o ciclo antes de publicar o job.
   Falha de publicação não pode apagar o ciclo e **deve** permitir
   reconciliação. O caminho Redis legado ainda existe quando a flag está em
   `false`, mas sua remoção foi aprovada e permanece como follow-up.
2. O ciclo **deve** registrar sequência por ticket, chaves de abertura/fechamento, snapshot seguro, vínculo ordenado de mensagens, status, `next_attempt_at`, marca de publicação e lease. Claims/transições **devem** usar estado esperado e exclusão concorrente; apenas um trabalhador pode reclamar o mesmo trabalho elegível.
3. `next_attempt_at` **deve** ser a fonte de elegibilidade. Reconciliadores **não podem** republicar antes dele nem duplicar job marcado como publicado. Backoff local e `Retry-After` **devem** usar o horário mais tardio aplicável.
4. A chave `ia:cycle:{cycle_id}` e a identidade persistida da classificação **devem** impedir análise terminal duplicada. Conclusão **deve** persistir classificação, snapshot e estado terminal; avisos resultam em `completed_with_warnings`.

## Histórico e contexto

1. O trabalhador **deve** recuperar todas as páginas do histórico, deduplicar e ordenar por timestamp/ID, limitar mensagens à fronteira do ciclo e salvar o snapshot/membership antes da classificação.
2. Bots, eventos técnicos, conteúdo invisível/excluído e tipos desconhecidos **devem** ser removidos com contagens auditáveis. Cliente e atendente **devem** permanecer cronológicos; atendente é contexto, não alvo. Citações usam excerto limitado.
3. Transcrição de áudio e extração de imagem disponíveis **devem** ser renderizadas. Documento somente preserva metadados seguros. Contexto acima do limite configurado **deve** ser segmentado sem cortar mensagens quando possível, resumido por blocos e só então classificado.

## Mídia, falhas e recuperação

1. Áudio/imagem **devem** possuir reserva PostgreSQL antes da fila Redis. Estado, tentativa, publicação, lease e transição **devem** impedir conclusão obsoleta após recuperação concorrente.
2. Mídia pendente ou recuperável **deve** levar o ciclo a `waiting_media` até a tentativa elegível. Falha terminal de áudio **deve** renderizar marcador seguro e pode concluir com aviso.
3. Falha terminal de imagem **deve** levar somente ciclos dependentes a `media_blocked`; tais ciclos **não podem** ser classificados sem recuperação da imagem. Extração bem-sucedida posterior **deve** acordar apenas os ciclos bloqueados que dependem dela.
4. Falhas transitórias, incluindo 429, 503 e timeout, **devem** manter estado durável, respeitar `Retry-After` e não consumir indevidamente a tentativa terminal. Falha permanente **deve** registrar motivo sanitizado. Recuperação direcionada **não pode** remover dead-letter não relacionado.

## Remoção do legado, observabilidade e verificação

O modo Redis-buffer, a flag `DIGISAC_HISTORY_FINALIZATION_ENABLED`, as chaves `buffer:*`, `ticket_close_task:*`, `ticket_classify_after:*` e `ticket_close_scheduled:*`, o debounce e o tratamento de buffer no IA worker serão removidos. A limitação de recuperação em réplica única também será eliminada. Esta seção de compatibilidade/legado será removida como parte do refactor.

Filas, dead letters e ciclos por estado **devem** ser consultáveis sem conteúdo sensível. Testes de banco descartável **devem** cobrir paginação, fronteira, filtro/renderização, claim/lease concorrente, persistência antes da fila, agenda futura, reconciliação sem duplicação, aviso de áudio e imagem bloqueada/acordada no modo persistente.

- Uma queda entre persistência e publicação é recuperável sem duplicar classificação.
- Imagem terminalmente falha nunca gera classificação do ciclo dependente.
- Múltiplos recuperadores reclamam apenas um job devido.

## Decisão registrada

Está decidido descontinuar e remover completamente o modo Redis-buffer. A flag, as chaves legadas, o debounce, o tratamento do worker, a limitação de réplica única e a cobertura específica legada serão removidos ou substituídos por equivalentes persistentes; somente o fluxo por histórico permanecerá.
