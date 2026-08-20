# SPEC-0003 — Finalização durável, contexto e mídia

- **Status:** baseline ativo, derivado da implementação; finalização persistente única; limite de ciclo consumido pelo mapeamento corrigido no issue 0020; retry durável de áudio alinhado à recuperação de mídia no issue 0027
- **Versão:** 1.5
- **Prioridade/Fase:** P0/P1 / operação durável e verificação
- **Rastreabilidade:** PRD §§5.3–5.4, 6 e 8; ARCHITECTURE §§4–7 e 12; `IMPLEMENTATION_PLAN.md` baseline concluído e trabalho pendente; Alembic `0013_conversation_cycles`, `0014_durable_retry_scheduling`; SPEC-0001–0002
- **Dependências:** SPEC-0001, SPEC-0002

## Status de implementação

Os contratos de ciclo persistente, reserva de mídia, agenda, publicação e
recuperação estão implementados no código atual. A verificação operacional
mais recente foi executada no runner PostgreSQL 16 descartável em 2026-08-14
com **36 testes aprovados, 143 desselecionados**, incluindo concorrência de ciclos, liberação após
falha de publicação, recuperação due-only de áudio/imagem e despertar seletivo
de ciclos bloqueados por imagem. Esta nota registra evidência local; não altera
o contrato nem afirma verificação de Redis, fornecedores ou produção.

**Integração de limite (2026-08-17):** o mapeamento departamental usa os
`cycle_started_at` e `ticket_closed_at` persistidos pelo ciclo. Quando esses
limites não estão disponíveis, a avaliação dependente permanece bloqueada; não
se infere uma fronteira a partir de atribuições posteriores.

**Retry de áudio (2026-08-20):** falhas transitórias de provider, timeout e
conexão permanecem `pending` com agenda durável além do limite de tentativas da
classificação IA. Dead-letters legados só são reabertos com evidência
persistida transitória; filas e dead-letters são deduplicados por mensagem, a
cópia de segurança é mantida até uma transcrição não vazia ser persistida e
erros armazenados/logados usam categorias sanitizadas. A evidência de execução
local é registrada no issue 0027 e não afirma Redis, fornecedores ou produção.

A verificação canônica de 2026-08-20 passou compileall, Pyright estrito,
**212 testes offline aprovados e 69 skips**, Alembic
`0020_cycle_contact_provenance` e **69 testes PostgreSQL aprovados, 212
desselecionados** no runner descartável. Os skips e resultados locais não
comprovam Redis, fornecedores ou produção.

## Objetivo e não objetivos

Definir a finalização por histórico DigiSac e os contratos de mídia, contexto e recuperação. Isso não define SLA de fornecedor, reprocessamento amplo, mudança de modelo ou resposta automática ao cliente.

## Modo persistente, ciclo e concorrência

1. Abertura/reabertura **deve** criar ou recuperar um ciclo persistente e
   fechamento **deve** persistir o ciclo antes de publicar o job. Falha de
   publicação não pode apagar o ciclo e **deve** permitir reconciliação.
2. O ciclo **deve** registrar sequência por ticket, chaves de abertura/fechamento, snapshot seguro, vínculo ordenado de mensagens, status, `next_attempt_at`, marca de publicação e lease. Claims/transições **devem** usar estado esperado e exclusão concorrente; apenas um trabalhador pode reclamar o mesmo trabalho elegível.
3. `next_attempt_at` **deve** ser a fonte de elegibilidade. Reconciliadores **não podem** republicar antes dele nem duplicar job marcado como publicado. Backoff local e `Retry-After` **devem** usar o horário mais tardio aplicável.
4. A chave `ia:cycle:{cycle_id}` e a identidade persistida da classificação **devem** impedir análise terminal duplicada. Conclusão **deve** persistir classificação, snapshot e estado terminal; avisos resultam em `completed_with_warnings`.

## Histórico e contexto

1. O trabalhador **deve** recuperar todas as páginas do histórico, deduplicar e ordenar por timestamp/ID, limitar mensagens à fronteira do ciclo e salvar o snapshot/membership antes da classificação.
2. Bots, eventos técnicos, conteúdo invisível/excluído e tipos desconhecidos **devem** ser removidos com contagens auditáveis. Cliente e atendente **devem** permanecer cronológicos; atendente é contexto, não alvo. Citações usam excerto limitado.
3. Transcrição de áudio e extração de imagem disponíveis **devem** ser renderizadas. Documento somente preserva metadados seguros. Contexto acima do limite configurado **deve** ser segmentado sem cortar mensagens quando possível, resumido por blocos e só então classificado.

4. Consumidores que derivam roteamento a partir do histórico de atribuições
   **devem** respeitar os limites persistidos do ciclo e permanecer bloqueados
   quando a fronteira necessária não estiver disponível.

## Mídia, falhas e recuperação

1. Áudio/imagem **devem** possuir reserva PostgreSQL antes da fila Redis. Estado, tentativa, publicação, lease e transição **devem** impedir conclusão obsoleta após recuperação concorrente.
2. Mídia pendente ou recuperável **deve** levar o ciclo a `waiting_media` até a tentativa elegível. Falha terminal de áudio **deve** renderizar marcador seguro e pode concluir com aviso.
3. Falha terminal de imagem **deve** levar somente ciclos dependentes a `media_blocked`; tais ciclos **não podem** ser classificados sem recuperação da imagem. Extração bem-sucedida posterior **deve** acordar apenas os ciclos bloqueados que dependem dela.
4. Falhas transitórias, incluindo 429, 503 e timeout, **devem** manter estado durável, respeitar `Retry-After` e não consumir indevidamente a tentativa terminal. Falha permanente **deve** registrar motivo sanitizado. Recuperação direcionada **não pode** remover dead-letter não relacionado.

## Remoção do legado, observabilidade e verificação

Filas, dead letters e ciclos por estado **devem** ser consultáveis sem conteúdo sensível. Testes de banco descartável **devem** cobrir paginação, fronteira, filtro/renderização, claim/lease concorrente, persistência antes da fila, agenda futura, reconciliação sem duplicação, aviso de áudio e imagem bloqueada/acordada no modo persistente.

- Uma queda entre persistência e publicação é recuperável sem duplicar classificação.
- Imagem terminalmente falha nunca gera classificação do ciclo dependente.
- Múltiplos recuperadores reclamam apenas um job devido.

## Decisão registrada

O modo Redis-buffer, a flag, suas chaves, debounce, tratamento no worker e
cobertura específica foram removidos. Somente o fluxo por histórico permanece;
Redis é transporte e coordenação transitória do fluxo persistente.
