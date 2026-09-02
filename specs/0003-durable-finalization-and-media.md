# SPEC-0003 — Finalização durável, contexto e mídia

- **Status:** baseline ativo, derivado da implementação; finalização persistente única por polling/lease PostgreSQL nos issues 0048–0050; áudio e imagem sem transporte Redis ativo nos issues 0049–0050; limite de ciclo consumido pelo mapeamento corrigido no issue 0020; retry durável de áudio alinhado à recuperação de mídia no issue 0027; gate compartilhado de áudio/imagem até conteúdo não vazio no issue 0046; boundaries estruturais nos issues 0029, 0031 e 0035; auditoria manual de resíduos Redis no issue 0037
- **Versão:** 2.1
- **Prioridade/Fase:** P0/P1 / operação durável e verificação
- **Rastreabilidade:** PRD §§5.3–5.4, 6 e 8; ARCHITECTURE §§4–7 e 12; `IMPLEMENTATION_PLAN.md`; Alembic `0013_conversation_cycles`, `0014_durable_retry_scheduling`, `0024_durable_media_leases`; SPEC-0001–0002; issues 0037, 0046, 0048, 0049 e 0050
- **Dependências:** SPEC-0001, SPEC-0002

## Status de implementação

Os contratos de ciclo persistente, reserva de mídia, agenda, polling/lease
PostgreSQL, recuperação e gate compartilhado de áudio/imagem estão implementados no código
atual. A verificação operacional mais recente foi executada no runner
PostgreSQL 16 descartável em 2026-08-26 com **78 testes aprovados, 258
desselecionados**, incluindo áudio terminal bloqueado, áudio pendente em espera,
conteúdo concluído não vazio e despertar seletivo por áudio/imagem. Esta nota
registra evidência local; não altera o contrato nem afirma verificação de Redis,
fornecedores ou produção.

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

**Nota estrutural (2026-08-20):** o issue 0029 isolou a persistência de ciclos,
membership de mensagens, projeções de resultado/métricas e recuperação seletiva
de mídia em `src/core/conversation_cycle_repository.py`. A mudança preserva as
assinaturas assíncronas, o pool único, a verificação do schema e os contratos de
transação, idempotência, claims, leases, publicação e privacidade; `src/core/db.py`
mantém o ciclo de vida do PostgreSQL e a fachada de compatibilidade. Não houve
alteração de schema, filas, workflow, provider ou semântica durável.

**Nota estrutural (2026-08-20):** o issue 0031 isolou a persistência compartilhada
de transcrições e extrações de imagem em `src/core/durable_media_repository.py`.
Reservas por mensagem, transições protegidas, leituras, recuperação due-only,
liberação de publicação e projeção de mídia pendente mantêm as assinaturas da
fachada, o pool único, as transações, `SKIP LOCKED`, leases e as regras de
privacidade. Não houve alteração de schema, filas, retry, workflow, provider ou
semântica durável.

**Nota estrutural (2026-08-20):** o issue 0035 isolou o contrato model-facing
de classificação em `src/core/ia_classification.py`. A finalização persistente
continua responsável por contexto, claims, transições, chamada do worker e
persistência; não houve alteração de ordenação do ciclo, mídia, retry,
idempotência, filas ou recuperação.

**Auditoria Redis (2026-08-21):** o issue 0037 reconciliou filas, dead-letters,
marcadores de publicação, agendas, leases e estados PostgreSQL antes e depois
de remover 857 chaves das seis famílias de buffer/debounce órfãs. A fila de
imagem, sua dead-letter, `ia_processing` e todo o estado durável permaneceram
intactos; a operação é manual, allowlisted e não altera o contrato de reserva,
publicação, retry ou recuperação.

**Finalização por polling PostgreSQL (2026-09-02):** o issue 0048 removeu
`ia_queue` e `ia_dead_letter` do caminho ativo da IA. Fechamento apenas persiste
o ciclo; o worker consulta um candidato due, aplica lease e processa a mesma
linha numa transação de claim com `FOR UPDATE SKIP LOCKED`. `next_attempt_at`
e a expiração do lease são a única elegibilidade. `enqueued_at` permanece como
campo de compatibilidade/observabilidade, não como marcador de publicação. Os
contadores `ia_due`, `ia_scheduled` e `ia_leased` vêm do PostgreSQL; o comando
manual `scripts/retire_legacy_ia_queue.py` inventaria uma fatia bounded da lista
legada e só remove, em modo explícito, itens com ciclo durável conhecido.

**Transcrição de áudio por polling PostgreSQL (2026-09-02):** o issue 0049
removeu `audio_transcription_queue` e `audio_transcription_dead_letter` do
caminho ativo. A reserva de `message_transcriptions` não publica mais no Redis;
`audio_worker` reclama uma linha due com `FOR UPDATE SKIP LOCKED`, owner e lease
explícitos. Retry transitório grava somente `next_attempt_at` e o erro
sanitizado; falha permanente grava `failed` no PostgreSQL. Queda do processo é
recuperada pela expiração do lease, sem lista de segurança ou deduplicação em
Redis. O script `scripts/retire_legacy_audio_queue.py` inventaria as duas listas,
 permite importar dead-letters transitórios com evidência persistida e só remove
 entradas seguras em modo explícito e bounded.

**Extração de imagem por polling PostgreSQL (2026-09-02):** o issue 0050
removeu `image_extraction_queue` e `image_extraction_dead_letter` do caminho
ativo. `image_worker` reclama uma linha due de
`message_image_extractions` com `FOR UPDATE SKIP LOCKED`, owner e lease; retry
transitório grava `next_attempt_at`, e falha permanente grava `failed` sem
criar outra cópia Redis. O script
`scripts/retire_legacy_image_queue.py` inventaria as listas legadas, preserva
IDs desconhecidos/malformados e dead-letters transitórios, e só remove valores
validados após confirmação explícita.

A verificação canônica de 2026-08-20 passou compileall, Pyright estrito,
**216 testes offline aprovados e 69 skips**, Alembic
`0020_cycle_contact_provenance` e **69 testes PostgreSQL aprovados, 216
desselecionados** no runner descartável. Os skips e resultados locais não
comprovam Redis, fornecedores ou produção.

Em 2026-09-02, a validação do issue 0050 passou compileall, Pyright estrito,
**280 testes aprovados e 84 skips** offline e **34 testes PostgreSQL** em banco
descartável com head `0024_durable_media_leases`. A cobertura inclui claim
concorrente de imagem, due/future schedule, lease/ownership, retry persistido,
gate de mídia, admissão por webhook/IA e regressões de áudio/imagem. Nenhum
resíduo Redis foi removido ou reproduzido; essa evidência não comprova provider,
deployment ou produção.

## Objetivo e não objetivos

Definir a finalização por histórico DigiSac e os contratos de mídia, contexto e recuperação. Isso não define SLA de fornecedor, reprocessamento amplo, mudança de modelo ou resposta automática ao cliente.

## Modo persistente, ciclo e concorrência

1. Abertura/reabertura **deve** criar ou recuperar um ciclo persistente e
   fechamento **deve** persistir o ciclo elegível ao polling PostgreSQL. O
   webhook não publica `ia_queue`; uma falha posterior de Redis não apaga nem
   torna invisível o ciclo durável.
2. O ciclo **deve** registrar sequência por ticket, chaves de abertura/fechamento, snapshot seguro, vínculo ordenado de mensagens, status, `next_attempt_at` e lease. Claims/transições **devem** usar estado esperado e exclusão concorrente; apenas um trabalhador pode reclamar o mesmo trabalho elegível.
3. `next_attempt_at` **deve** ser a fonte de elegibilidade. O claim de um ciclo
   due **deve** selecionar e gravar owner/expiração na mesma transação com
   `FOR UPDATE SKIP LOCKED`; a expiração do lease recupera crash/restart. Backoff
   local e `Retry-After` **devem** usar o horário mais tardio aplicável. Janela
   local de provider é verificada antes de reconciliar mídia ou reclamar ciclo.
4. A chave `ia:cycle:{cycle_id}` e a identidade persistida da classificação **devem** impedir análise terminal duplicada. Conclusão **deve** persistir classificação, snapshot e estado terminal; avisos resultam em `completed_with_warnings`.

## Histórico e contexto

1. O trabalhador **deve** recuperar todas as páginas do histórico, deduplicar e ordenar por timestamp/ID, limitar mensagens à fronteira do ciclo e salvar o snapshot/membership antes da classificação.
2. Bots, eventos técnicos, conteúdo invisível/excluído e tipos desconhecidos **devem** ser removidos com contagens auditáveis. Cliente e atendente **devem** permanecer cronológicos; atendente é contexto, não alvo. Citações usam excerto limitado.
3. Transcrição de áudio e extração de imagem disponíveis **devem** ser renderizadas. Documento somente preserva metadados seguros. Contexto acima do limite configurado **deve** ser segmentado sem cortar mensagens quando possível, resumido por blocos e só então classificado.

4. Consumidores que derivam roteamento a partir do histórico de atribuições
   **devem** respeitar os limites persistidos do ciclo e permanecer bloqueados
   quando a fronteira necessária não estiver disponível.

## Mídia, falhas e recuperação

1. Áudio/imagem **devem** possuir reserva PostgreSQL antes de qualquer transporte. Ambos devem ser reclamados diretamente do PostgreSQL; filas Redis legadas não participam do trabalho ativo. Estado, tentativa, publicação, lease e transição **devem** impedir conclusão obsoleta após recuperação concorrente.
2. Mídia pendente ou recuperável **deve** levar o ciclo a `waiting_media` até a tentativa elegível. Somente estado `completed` com texto extraído não vazio **pode** satisfazer o gate de contexto.
3. Falha terminal de áudio ou imagem **deve** levar somente ciclos dependentes a `media_blocked`; tais ciclos **não podem** ser classificados, receber marcador sintético ou gerar `completed_with_warnings` por mídia ausente. Recuperação bem-sucedida posterior **deve** acordar apenas os ciclos bloqueados que dependem dela.
4. Falhas transitórias, incluindo 429, 503 e timeout, **devem** manter estado durável, respeitar `Retry-After` e não consumir indevidamente a tentativa terminal. Falha permanente **deve** registrar motivo sanitizado. Recuperação direcionada **não pode** remover dead-letter não relacionado.

## Remoção do legado, observabilidade e verificação

Filas, dead letters e ciclos por estado **devem** ser consultáveis sem conteúdo sensível. `ia_due`, `ia_scheduled` e `ia_leased`, além dos contadores `audio_*` e `image_*` de due, agendado, lease, stale, completed e failed, **devem** ser derivados de PostgreSQL. As listas Redis de IA e mídia são somente visibilidade de resíduos de cutover. Testes de banco descartável **devem** cobrir paginação, fronteira, filtro/renderização, claim/lease concorrente, persistência antes do polling, agenda futura, cooldown sem claim, crash/restart sem Redis, reconciliação de mídia e áudio/imagem bloqueados e acordados no modo persistente.

- Uma queda entre persistência e polling é recuperável sem duplicar classificação.
- Áudio ou imagem terminalmente falha nunca gera classificação do ciclo dependente.
- Múltiplos recuperadores reclamam apenas um job devido.

## Decisão registrada

O modo Redis-buffer, a flag, suas chaves, debounce, tratamento no worker e
cobertura específica foram removidos. A fila Redis persistente de finalização
IA também foi retirada: somente o fluxo por histórico com polling/lease
PostgreSQL permanece. Redis continua transporte/coordenação transitória para
fluxos que ainda o requerem; áudio e imagem não dependem mais de Redis.
Resíduos históricos não ativos são tratados somente por
auditoria manual bounded; `ia_processing`, itens malformados, IDs desconhecidos
e dead-letters transitórios não importados não são apagados por inferência.
