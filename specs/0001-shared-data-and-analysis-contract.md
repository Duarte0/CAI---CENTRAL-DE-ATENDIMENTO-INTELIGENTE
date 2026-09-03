# SPEC-0001 — Contrato compartilhado de dados e análise

- **Status:** baseline ativo, derivado da implementação; issues 0010, 0024, 0025 e 0032 corrigiram a paridade da taxonomia no prompt, a fronteira de privacidade dos logs e a separação da persistência de classificações; issue 0035 isolou o contrato model-facing sem alterar a política; issue 0037 adicionou auditoria manual e allowlist para resíduos Redis sem alterar a autoridade durável; issues 0049 e 0050 retiraram o transporte Redis ativo de áudio e imagem; issue 0051 preservou o backoff durável de hydration; issue 0052 adicionou o contexto de manutenção e a retirada validada por relatório; issue 0053 moveu a idempotência genérica de webhook para PostgreSQL; issue 0054 retirou o produtor das views IA de status/resultado e deixou sua limpeza para o sunset bounded; decisões de produto registradas abaixo
- **Versão:** 1.11
- **Prioridade/Fase:** P0 / baseline de requisitos
- **Rastreabilidade:** PRD §§3, 6 e 8; ARCHITECTURE §§4, 8–9 e 12; `IMPLEMENTATION_PLAN.md` baseline concluído e trabalho pendente; Alembic `0001_initial`–`0025_webhook_event_keys`; SPEC-0007–0010; issues 0010, 0024, 0025, 0032, 0035, 0037, 0049, 0050, 0051, 0052, 0053 e 0054
- **Dependências:** nenhuma

## Objetivo e não objetivos

Definir os contratos transversais de dados, classificação e segurança que tornam uma análise auditável, idempotente e recuperável. PostgreSQL é a fonte durável; Redis é somente transporte/coordenação transitória onde um fluxo ainda o requer e não é a fila persistente de finalização IA, a autoridade da idempotência genérica de webhook ou a fonte de status/resultado da IA.

Esta especificação registra retenção indefinida, sem exclusão ou arquivamento automático. Exclusão manual direta no PostgreSQL pode ocorrer caso a caso. Não há mudanças de schema de retenção, jobs de limpeza ou automação orientada pela LGPD planejados. Os issues 0037 e 0052 adicionam somente auditorias manuais, bounded e repetíveis para famílias Redis órfãs e listas legadas já fora do transporte ativo; não apagam dados duráveis nem transformam a operação em job de retenção.

## Estado de referência

`PRD.md` e `ARCHITECTURE.md` são baselines presentes, derivados da implementação. Código, migrations e configuração determinam o comportamento atual; esta especificação consolida o contrato que o trabalho pendente deve preservar. O schema é Alembic-owned até `0025_webhook_event_keys`; inicialização da aplicação deve apenas verificar o schema e não pode criar nem mutar tabelas.

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

**Nota estrutural (2026-08-20):** o issue 0032 isolou a persistência de
classificações, vínculos ordenados de mensagens, atualização de protocolo e
consultas de existência em `src/core/classification_repository.py`. A fachada
`src/core/db.py` preserva os exports assíncronos e mantém o pool único, a
verificação de schema e os primitives compartilhados; não houve alteração de
schema, identidade UUIDv7, idempotência, transação, contrato de IA, protocolo,
privacidade ou semântica histórica.

**Nota estrutural (2026-08-20):** o issue 0035 isolou o contrato model-facing
em `src/core/ia_classification.py`: prompts, recuperação/validação do JSON,
normalização e diagnósticos sanitizados não dependem de provider, Redis,
PostgreSQL ou estado de ciclo. `src/workers/ia_worker.py` continua dono da
chamada Groq, erros/cooldown, enriquecimento, persistência e orquestração. Não
houve alteração da taxonomia, contrato de quatro campos, privacidade ou
semântica de retry.

**Auditoria Redis (2026-08-21):** o issue 0037 confirmou que PostgreSQL continua
sendo a autoridade durável e que Redis contém somente transporte, idempotência
temporária e visões TTL. O comando manual `scripts/redis_residue_cleanup.py`
produz dry-run com tipos, TTLs, donos, classificação, estado de filas e
reconciliação PostgreSQL; sua allowlist remove apenas famílias órfãs revisadas,
retém `ia_processing` inconclusivo e não toca `processed:*`, resultados
transitórios, filas, dead-letters ou registros PostgreSQL.

**Transcrição durável (2026-09-02):** o issue 0049 estende a fronteira: a
reserva, agenda, owner, lease, retry e resultado de áudio vivem em
`message_transcriptions`; `audio_worker` não precisa de Redis nem publica listas.
Listas legadas de áudio são somente evidência de cutover e não podem ser usadas
como fonte de backlog.

**Hydration de contatos (2026-09-03):** o issue 0051 mantém referências
repetidas no limite PostgreSQL: locks por contato e a decisão normal não podem
limpar ou antecipar um `next_attempt_at` futuro. `pending`, `running`, falha
devida e hydration sucedida são observados como no-op apropriado; o poller
continua dono de claims, lease expiry e retry. Não há nova fila Redis nem retry
forçado sem contrato administrativo aprovado.

**Extração de imagem durável (2026-09-02):** o issue 0050 aplica a mesma
fronteira a `message_image_extractions`; reserva, agenda, owner, lease, retry e
resultado são PostgreSQL, e `image_worker` reclama linhas due diretamente sem
publicar ou consumir `image_extraction_queue`. A lista e a dead-letter legadas
permanecem apenas para inventário bounded e retirada manual validada. Redis
continua permitido somente para coordenação transitória e famílias explicitamente
retidas; não é autoridade, transporte de mídia ou fonte das views IA de
status/resultado.

**Retirada validada de filas legadas (2026-09-03):** o issue 0052 adiciona o
target Docker/perfil Compose `maintenance` e o coordenador
`scripts.retire_validated_legacy_redis_queues`. O dry-run completo registra
operador, revisão, projeto Compose, estado de `/health` e `/queues`, revisão do
schema, invariantes agregadas do PostgreSQL e inventários das seis listas
legadas. Relatórios guardam apenas digests SHA-256 dos valores Redis. O apply
exige relatório revisado, snapshot posterior sem crescimento/troca, recovery
point aprovado, confirmação exata e uma família por vez; usa somente `LREM`
exato nas entradas validadas. Valores malformados, desconhecidos e
dead-letters transitórios permanecem; o comando não republica, chama provider,
altera PostgreSQL ou toca outras famílias Redis.

**Idempotência genérica de webhook em PostgreSQL (2026-09-03):** o issue 0053
move a decisão de uma entrega vencedora para `webhook_event_keys`, com digest
SHA-256 minúsculo, `first_seen_at`, `expires_at` de uma hora e índice para
limpeza bounded. A decisão usa uma única operação PostgreSQL concorrente:
entregas dentro da janela retornam `duplicate`, e somente um conflito expirado
pode ser substituído. A indisponibilidade do banco falha fechadamente e não
autoriza um `202` de aceite. O ledger não guarda corpo, texto, contato, segredo,
URL assinada ou resposta de provider; limpeza é independente do caminho de
requisição, limitada por lote e observável apenas por contagens.

O handoff de `processed:*` é uma operação única e explícita: a API antiga é
parada e drenada, um dry-run completo com revisão/backup é arquivado, os
marcadores ainda vivos são importados sem apagar suas chaves Redis, e só então
a API PostgreSQL-only é iniciada. Uma fotografia nova ou truncada interrompe o
apply. Isso evita a lacuna de uma implantação mista; a remoção das chaves
retidas continua pertencendo aos issues 0054–0056.

**Views IA Redis aposentadas (2026-09-03):** o issue 0054 removeu de
`src/workers/ia_worker.py` a publicação de `ia_status:*` e `ia_result:*`,
removeu `RESULT_TTL_SECONDS` da configuração do worker e retirou a dependência
Redis do serviço IA. As rotas públicas continuam consultando os repositórios
PostgreSQL. O comando `scripts.retire_ia_redis_compatibility` faz inventário
bounded com buckets de TTL, digests de entrada e matches duráveis sem expor
valores; o importador histórico foi mantido somente no perfil `maintenance`.
Nenhuma chave foi removida nesta etapa: o apply requer decisão histórica,
segunda fotografia e uma janela completa de 86400 segundos.

## Contrato de dados e integridade

1. PostgreSQL **deve** persistir classificações, vínculos ordenados de mensagens, estados/resultados de mídia, histórico de atribuição, diretórios DigiSac e Acessórias, ciclos persistentes e a decisão genérica de idempotência de webhook. O ciclo IA e as mídias elegíveis **devem** ser reclamados por lease PostgreSQL; Redis limita-se à coordenação de fluxos que ainda tenham contrato próprio e não é fonte das views IA de status/resultado. Filas legadas de IA e mídia não são backlog ativo e só podem ser removidas por inventário completo, reconciliado e confirmação delimitada.
2. Cada classificação **deve** ter identidade interna e `public_id` UUIDv7 único. Quando `idempotency_key` for fornecida, ela **deve** ser única e não vazia; tentativas concorrentes com a mesma chave **devem** devolver a mesma classificação sem duplicar linhas ou vínculos. O digest genérico de webhook **deve** ser único no ledger enquanto `expires_at` não vencer, com substituição expirada atômica e retenção equivalente a uma hora.
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
- A retirada de listas legadas **deve** ocorrer em contexto de manutenção
  separado do `api`, com `DATABASE_URL` e `REDIS_URL` protegidos, revisão do
  checkout, operador, relatório arquivado e recovery point registrado. Apply
  **deve** ser por família, precedido de dry-run completo e segundo snapshot;
  valores Redis brutos, credenciais, corpos, payloads e URLs assinadas não
  podem entrar no relatório.
- O sunset de `ia_status:*` e `ia_result:*` **deve** usar somente
  `scripts.retire_ia_redis_compatibility`, exigir reconciliação histórica,
  digests de chave/entrada, segunda fotografia e uma observação de pelo menos
  um TTL completo antes do apply. O comando **não pode** tocar `processed:*`,
  filas, `ia_processing` ou dados PostgreSQL.
- Solicitações normais de hydration **devem** distinguir nos logs sanitizados
  uma nova solicitação, duplicação, backoff futuro preservado, falha já devida
  e estado sucedido/current; nenhum desses eventos pode antecipar
  `next_attempt_at` ou introduzir fila Redis.
- Os diagnósticos de extração do webhook **devem** registrar somente evento seguro,
  presença/tipo e caminho de origem; valores extraídos, corpo bruto, URLs e
  segredos não podem atravessar essa fronteira de log.
- `GET /health` **deve** verificar Redis e PostgreSQL e retornar `503` quando o banco não estiver pronto.
- Os dados **devem** ser retidos indefinidamente por seu valor histórico, analítico e futuro uso na construção de FAQ sobre o corpus de classificações. Não são planejados migração de retenção, job de limpeza ou automação LGPD; exclusão manual direta no PostgreSQL é permitida caso a caso.

## Testes e aceitação

Testes de evolução PostgreSQL e de identificadores **devem** cobrir UUIDv7, unicidade, idempotência concorrente do ledger de webhook, ordem/constraints de vínculos e agenda durável. Testes do worker **devem** cobrir contrato IA, taxonomia, título/protocolo, rejeição de saída incompleta e ausência de conteúdo sensível nos diagnósticos do parser. Testes de hydration **devem** provar que referências repetidas preservam um backoff futuro e deixam falhas devidas para o poller. Migrations/backfills **devem** ser verificados contra banco descartável, e o handoff de marcadores deve ser report-bound, sem apagar sua fonte Redis.

- A mesma chave concorrente produz uma única classificação e o mesmo `public_id` para todos os chamadores.
- Nenhuma saída sem os quatro campos contratuais produz um resultado persistido válido.
- Uma evolução de schema prova preservação de dados ou recusa explícita antes de uma operação destrutiva.

## Decisões registradas

Retenção, privacidade e interpretação do histórico de atribuição estão decididas: os dados permanecem indefinidamente, e todas as transferências observadas são preservadas cronologicamente. A integração Acessórias aprovada poderá usar esse histórico para contexto de mapeamento departamental; este contrato não implementa roteamento nem criação de Request. Não há filtragem por relevância de negócio.
