# Digisac Conversation Analyzer

API em FastAPI para receber webhooks do Digisac, agrupar mensagens de uma conversa e usar OpenAI para identificar dúvidas do cliente. Esta etapa apenas classifica e armazena a análise; não abre chamados nem integra sistemas externos.

## Arquitetura

```text
Digisac webhook (`message.created`)
  → FastAPI
  → Redis: buffer por ticket
  → audio_transcription_queue / image_extraction_queue
  → workers de mídia → PostgreSQL
Digisac webhook (`ticket.updated`, `isOpen: false`)
  → PostgreSQL: histórico cronológico de departamento/atendente
  → Redis: ia_queue (um job por ticket)
  → aguarda brevemente mídias pendentes e monta o contexto consolidado
  → ia_worker (OpenAI)
  → Redis: resultado, status e ia_dead_letter
```

Redis é usado tanto como cache quanto como fila. RabbitMQ não faz parte desta implementação.
PostgreSQL é a única persistência durável; Redis permanece reservado a filas,
debounce, buffers, locks, idempotência temporária e estados transitórios.

### Garantias atuais

- O payload é validado com Pydantic e eventos repetidos são ignorados por idempotência.
- Quando `WEBHOOK_SECRET` está definido, o webhook exige o cabeçalho `X-Digisac-Signature` com HMAC-SHA256 do corpo bruto. São aceitos `<hash>` e `sha256=<hash>`.
- A atualização do buffer é atômica no Redis, evitando perda de mensagens em requisições simultâneas. Cliente e atendente ficam em ordem cronológica, como `Cliente:` e `Atendente:`.
- A IA só é chamada no fechamento do ticket. Mensagens recebidas depois que o fechamento foi agendado são registradas como anomalia e não reabrem o ciclo.
- O worker de IA move a mensagem para `ia_processing` enquanto analisa. Na inicialização, trabalhos deixados nessa lista por uma queda anterior retornam para `ia_queue`.
- Falhas da IA são reenfileiradas até `MAX_RETRY_ATTEMPTS`; após o limite, seguem para `ia_dead_letter` e o status fica `failed`.
- Áudios e imagens são processados fora do webhook. Extrações concluídas entram no
  contexto como `[áudio transcrito]` e `[imagem]`; em falha ou timeout, permanece o
  placeholder seguro.

> A recuperação de `ia_processing` pressupõe uma única réplica do worker de IA, que é a configuração atual do Compose. Para várias réplicas, a evolução indicada é Redis Streams com consumer groups.

## Requisitos

- Python 3.11 ou superior
- PostgreSQL 16 e Redis 7 (ou Docker/Docker Compose)
- `GROQ_API_KEY` para executar o worker de IA

## Configuração

Crie o arquivo de ambiente a partir do exemplo:

```bash
cp .env.example .env
```

Principais variáveis:

| Variável | Descrição | Padrão |
| --- | --- | --- |
| `GROQ_API_KEY` | Chave usada exclusivamente pelo worker de IA. | — |
| `WEBHOOK_SECRET` | Ativa a assinatura HMAC do webhook. | vazio (desativada) |
| `REDIS_URL` | URL de conexão Redis. | `redis://localhost:6379` |
| `DATABASE_URL` | URL de conexão PostgreSQL. | `postgresql://cai:senha@localhost:5432/cai` |
| `DATABASE_POOL_MIN_SIZE` | Conexões mínimas do pool por processo. | `1` |
| `DATABASE_POOL_MAX_SIZE` | Conexões máximas do pool por processo. | `10` |
| `POSTGRES_DB` | Banco criado pelo PostgreSQL. | `cai` |
| `POSTGRES_USER` | Usuário do PostgreSQL. | `cai` |
| `POSTGRES_PASSWORD` | Senha fornecida somente pelo ambiente. | — |
| `TICKET_BUFFER_TTL_SECONDS` | Retenção do contexto de um ticket ainda aberto. | `2592000` (30 dias) |
| `IA_TIMEOUT_SECONDS` | Tempo máximo de uma chamada à IA. | `60` |
| `MAX_RETRY_ATTEMPTS` | Número de novas tentativas após falha da IA. | `3` |
| `IA_RETRY_BASE_SECONDS` | Backoff inicial de falhas transitórias da Groq na classificação. | `2` |
| `IA_RETRY_MAX_DELAY_SECONDS` | Teto do backoff local da IA; `Retry-After` maior prevalece. | `900` |
| `IA_RETRY_PROVIDER_MARGIN_SECONDS` | Margem sobre o prazo informado pela Groq. | `1` |
| `MODEL_NAME` | Modelo utilizado pela análise de IA via Groq. | `openai/gpt-oss-120b` |
| `AUDIO_TRANSCRIPTION_MODEL` | Modelo utilizado para transcrever áudios. | `whisper-large-v3-turbo` |
| `IMAGE_VISION_MODEL` | Modelo multimodal usado para texto/resumo de imagens. | `qwen/qwen3.6-27b` |
| `IMAGE_VISION_MAX_COMPLETION_TOKENS` | Limite inicial de saída da API multimodal, mantendo margem no TPM. | `5000` |
| `IMAGE_MAX_BYTES` | Tamanho máximo de imagem baixada para processamento. | `4194304` |
| `IMAGE_RETRY_BASE_SECONDS` | Backoff inicial para falhas transitórias da extração de imagem. | `2` |
| `IMAGE_RETRY_MAX_DELAY_SECONDS` | Teto do backoff local; um `Retry-After` maior do provedor continua sendo respeitado. | `900` |
| `IMAGE_RETRY_PROVIDER_MARGIN_SECONDS` | Margem adicionada ao prazo de retry informado pelo provedor. | `1` |
| `IMAGE_DEAD_LETTER_RECOVERY_INTERVAL_SECONDS` | Intervalo de reconciliação automática de dead-letters transitórias antigas. | `60` |
| `CONTENT_RECOVERY_LEASE_SECONDS` | Lease após o qual mídia pendente/em processamento pode ser republicada. | `300` |
| `CONTENT_RECONCILE_INTERVAL_SECONDS` | Intervalo para publicar mídias cujo retry persistido venceu. | `5` |
| `DATABASE_STATEMENT_TIMEOUT_MS` | Timeout por statement nas conexões da aplicação. | `15000` |
| `DATABASE_LOCK_TIMEOUT_MS` | Tempo máximo aguardando lock nas conexões da aplicação. | `3000` |
| `CONTENT_EXTRACTION_WAIT_SECONDS` | Espera compartilhada por áudio/imagem antes da classificação. | `30` |
| `DIGISAC_DIRECTORY_SYNC_INTERVAL_SECONDS` | Intervalo da sincronização de departamentos e usuários. | `86400` |
| `DIGISAC_DIRECTORY_REFRESH_COOLDOWN_SECONDS` | Intervalo mínimo entre refreshes sob demanda por IDs desconhecidos. | `900` |
| `PROMPT_VERSION` | Versão identificadora do prompt de classificação. | `v2` |

## Execução com Docker

```bash
docker compose up --build
```

O Compose inicia PostgreSQL, Redis, o serviço único de migrations, API e os
workers. API e workers só iniciam depois de PostgreSQL saudável e migrations
aplicadas. A porta do PostgreSQL não é publicada por padrão.
A API fica disponível em `http://localhost:8000`.

## Execução local

Com PostgreSQL e Redis acessíveis em `DATABASE_URL` e `REDIS_URL`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn main:app --reload
python -m src.workers.ia_worker
python -m src.workers.audio_worker
python -m src.workers.image_worker
```

## API

| Método | Rota | Descrição |
| --- | --- | --- |
| `POST` | `/webhook/digisac` | Recebe um evento e responde `202 Accepted`. |
| `GET` | `/conversations/{conversation_id}/status` | Retorna estado, erros, tentativas e resultado quando concluído. |
| `GET` | `/conversations/{conversation_id}/result` | Retorna somente o resultado de IA concluído. |
| `GET` | `/queues` | Retorna tamanhos de `ia_queue` e `ia_dead_letter`. |
| `GET` | `/health` | Confirma conectividade com Redis e PostgreSQL. |

Exemplo de evento:

```json
{
  "event": "message.created",
  "conversation_id": "conv-123",
  "message_id": "msg-1",
  "content": "Não consigo emitir a nota fiscal. Como resolvo?",
  "sender_id": "customer-9"
}
```

Exemplo de análise concluída:

```json
{
  "intent_type": "question",
  "confidence": 0.98,
  "title": "Erro ao emitir nota fiscal",
  "description": "O cliente informa que não consegue emitir notas fiscais.",
  "department": ["Atendimento", "Departamento Fiscal"],
  "agent": ["Jaqueline Oliveira", "Carlos Silva"],
  "message_count": 1
}
```

### Histórico de departamentos e atendentes

Todo `ticket.updated` com `departmentId` ou `userId` registra uma transição na
tabela `ticket_assignment_history`, inclusive enquanto o ticket ainda está
aberto. A idempotência usa o identificador exclusivo do webhook quando
disponível; caso contrário, usa um SHA-256 estável dos IDs e timestamp da fonte.
Atualizações que mantêm o mesmo par `(departmentId, userId)` não criam uma nova
atribuição, mas a troca de atendente dentro do mesmo departamento cria.

Os nomes vêm exclusivamente de `/api/v1/departments` e `/api/v1/users`. A API
sincroniza todas as páginas na inicialização, sem bloquear o webhook, e repete a
sincronização diariamente. Um ID desconhecido pode disparar refresh controlado
no worker; se continuar desconhecido, ele é mantido no histórico e omitido das
listas de nomes sem interromper a classificação.

`department` e `agent` são montados pelo código em ordem cronológica, sem nomes
repetidos, e persistidos como JSON junto à classificação. Eles não são incluídos
no prompt, no exemplo de saída pedido à IA ou no parser da resposta do modelo.

O histórico é completo somente para eventos recebidos após a implantação desta
persistência. `ticketTransferCount` é guardado como diagnóstico, mas não contém
os IDs anteriores e nunca é usado para inventar transferências. A API de ticket
consultada fornece apenas a atribuição atual; não foi identificado um endpoint
utilizável para reconstruir o histórico anterior.

### Protocolo e título de exibição

Em eventos `ticket.updated`, o protocolo é capturado somente quando
`data.isOpen` é exatamente `false` e `data.id` e `data.protocol` estão
preenchidos. O valor é associado à análise por `conversation_id` e salvo no
campo opcional `protocol`; o campo `title` continua contendo exclusivamente o
título gerado pela IA.

As respostas de resultado apresentam também `display_title`, calculado como
`[{protocol}] - {title}` quando há protocolo, ou apenas `{title}` nos registros
antigos. O protocolo nunca é incluído no prompt nem solicitado à IA.

O fechamento mantém `ticket_protocol:{conversation_id}` temporariamente no
Redis antes de atualizar o PostgreSQL. Assim, se o webhook chegar antes da análise,
o worker aplica o valor durante a inserção; se a análise já existir, ela é
atualizada imediatamente. A mesma chave e um `UPDATE` idempotente tornam o
reprocessamento do mesmo webhook seguro, sem concatenar ou duplicar protocolos.

Com assinatura habilitada, calcule o HMAC-SHA256 sobre os bytes exatos do corpo e envie-o em `X-Digisac-Signature`.

## Testes

```bash
pytest -q
```

Os testes sem dependências externas cobrem modelos, idempotência, validação de
assinatura, retries, fila de falhas e recuperação de itens em processamento.
Os testes de persistência usam PostgreSQL real:

```bash
docker compose -f docker-compose.test.yml up -d --wait
export DATABASE_URL="postgresql://cai_test:cai_test@localhost:5433/cai_test"
CAI_TEST_DATABASE_URL="$DATABASE_URL" pytest -q --ignore=tests/test_webhook_local.py
docker compose -f docker-compose.test.yml down
```

Em um ambiente Compose, o serviço `migrate` aplica o schema antes dos serviços
de aplicação.

## Operação

- Um resultado permanece em `ia_result:{conversation_id}` pelo período configurado em `RESULT_TTL_SECONDS` (padrão: 24 horas).
- Consulte `/queues` para acompanhar acúmulo de trabalho e itens que exigem intervenção em `ia_dead_letter`.
- Consulte `/conversations/{conversation_id}/status` para verificar `pending`, `processing`, `completed` ou `failed`.

Backup e restauração básicos do PostgreSQL (com os serviços em execução):

```bash
mkdir -p backups
docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > backups/cai-$(date +%Y%m%d-%H%M%S).dump
cat backups/cai-YYYYMMDD-HHMMSS.dump | \
  docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" --clean --if-exists
```

### Instrução enviada à IA

Antes, a instrução dizia que o texto era consolidado somente das mensagens do
cliente, prefixadas por `Cliente:`. Agora ela informa que o contexto contém as
duas partes, em ordem cronológica e prefixadas por `Cliente:`/`Atendente:`;
instrui o modelo a classificar exclusivamente a intenção do cliente e a usar
as falas do atendente apenas como contexto. O schema de resposta e as cinco
categorias de `intent_type` não foram alterados.

### Histórico de classificações e migrations

Além do resultado temporário no Redis, cada classificação é mantida no
PostgreSQL. Os campos `message_ids`, `department` e `agent` são `JSONB`; os
timestamps persistentes são `TIMESTAMPTZ`.

As alterações de schema são versionadas com Alembic. A aplicação não cria nem
altera tabelas automaticamente:

```bash
DATABASE_URL="$DATABASE_URL" alembic upgrade head
```

Para bancos existentes, a evolução de identidade pública é deliberadamente
dividida. Não execute `upgrade head` diretamente antes dos backfills:

```bash
# 1. Migrations expansivas, FK ainda não validada e índices online.
DATABASE_URL="$DATABASE_URL" alembic upgrade 0009_recovery_indexes

# 2. Implante esta versão da API e dos workers, confirme nos logs que todos
#    reconheceram identity=true/idempotency=true/normalized_messages=true.

# 3. Inspeção somente leitura e backfills reiniciáveis.
python -m src.utils.audit_postgres --database-url "$DATABASE_URL"
python -m src.utils.backfill_classification_public_ids \
  --database-url "$DATABASE_URL"
python -m src.utils.backfill_classification_messages \
  --database-url "$DATABASE_URL"
python -m src.utils.backfill_classification_public_ids \
  --database-url "$DATABASE_URL" --apply
python -m src.utils.backfill_classification_messages \
  --database-url "$DATABASE_URL" --apply

# 4. Repita os dry-runs; ambos devem indicar zero pendências.
python -m src.utils.backfill_classification_public_ids \
  --database-url "$DATABASE_URL"
python -m src.utils.backfill_classification_messages \
  --database-url "$DATABASE_URL"

# 5. Somente então finalize NOT NULL/UNIQUE e valide a FK.
DATABASE_URL="$DATABASE_URL" alembic upgrade head
```

Durante a primeira fase, mantenha
O Compose aplica `ALEMBIC_TARGET_REVISION=head` por padrão. Em instalações
anteriores que ainda fixam `0009_recovery_indexes`, conclua os backfills
documentados antes de remover o pin. O `public_id` é UUIDv7 e aparece de
forma aditiva como `classification_public_id` nos novos resultados do Redis e
no endpoint de resultado; o `id BIGINT` interno permanece inalterado.

Rollback da fase expansiva:

```bash
DATABASE_URL="$DATABASE_URL" alembic downgrade 0001_initial
```

O downgrade de `0007_class_messages` recusa remover a tabela quando
ela contém dados. Isso é intencional: volte a aplicação para a versão
compatível e preserve o schema expandido até definir uma reversão de dados.
Após `public_id` ser finalizado, o downgrade para `0010_public_id_check`
remove apenas a obrigatoriedade, preservando os UUIDs existentes.
Da mesma forma, `0005_class_identity` não remove colunas de identidade
preenchidas; exporte e planeje essa contração explicitamente.
Como `CREATE/DROP INDEX CONCURRENTLY` exige blocos autocommit, um downgrade
recusado pode já ter parado numa revision intermediária segura. Consulte
`alembic current`, confirme que os dados protegidos permanecem presentes e
execute `alembic upgrade head` para restaurar a expansão; não tente forçar a
remoção das proteções.

### Retenção e manutenção

Nenhuma exclusão automática foi ativada. Classificações, snapshots de
departamento/agente, extrações e histórico de atribuições permanecem registros
de auditoria. A FK de `ticket_assignment_history.event_key` usa `RESTRICT`, de
modo que uma limpeza futura das chaves de idempotência deve excluir somente
chaves sem histórico e após uma janela de retenção aprovada.

Use `python -m src.utils.audit_postgres` periodicamente para acompanhar tamanho,
tuplas mortas, scans e uso de índices. Configure alertas para crescimento,
sessões `idle in transaction`, falhas de autovacuum e constraints não
validadas. Antes de criar TTL ou arquivamento, defina por tabela a retenção
mínima legal/operacional; o projeto deliberadamente não assume esse prazo.

Para localizar casos com baixa confiança:

```sql
SELECT id, conversation_id, confidence, title, created_at
FROM ia_classifications
WHERE confidence < 0.60
ORDER BY confidence ASC, created_at DESC;
```

### Migração SQLite → PostgreSQL

O arquivo SQLite original deve ser preservado e aberto somente para leitura.
Em um banco PostgreSQL vazio, execute. No Compose, o volume do SQLite é
montado somente nesta execução controlada e em modo somente leitura:

```bash
docker compose up -d postgres
docker compose run --rm migrate alembic upgrade head
mkdir -p migration-reports
docker compose run --rm \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/data:/legacy-data:ro" \
  -v "$PWD/migration-reports:/reports" migrate \
  sh -c 'python -m src.utils.migrate_sqlite_to_postgres \
    --sqlite-path /legacy-data/ia_history.db \
    --database-url "$DATABASE_URL" \
    --report /reports/cai-migration-report.json'
```

Fora do Compose, o mesmo módulo pode ser executado com Python instalado e
um `DATABASE_URL` que seja acessível pelo host.

O comando recusa destinos que já tenham registros, valida IDs, listas JSON,
timestamps e chaves de todas as tabelas, e faz rollback completo se houver
erro. Ele nunca apaga ou altera o SQLite.

Para migrar resultados que já existiam no Redis antes da ativação do PostgreSQL:

```bash
docker compose exec ia_worker python -m src.utils.backfill_redis_history
```

Esse processo é idempotente, mas os resultados antigos do Redis podem não ter
mais contexto ou IDs de mensagens.

### Cutover e rollback

1. Faça uma cópia segura e verificável de `ia_history.db`:
   `cp --preserve=all data/ia_history.db data/ia_history.db.pre-postgres` e
   registre `sha256sum data/ia_history.db.pre-postgres`.
2. Suba PostgreSQL e Redis e aguarde o serviço `migrate` concluir.
3. Pare API e workers que ainda apontem para SQLite.
4. Execute o migrador contra um PostgreSQL vazio e confira o relatório.
5. Suba os serviços com `DATABASE_URL`.
6. Verifique `/health`, novas classificações e reservas de mídia.

Em uma falha imediata, pare os serviços e faça o rollback usando a versão
anterior do código (ou imagem) que ainda contém o adaptador SQLite, apontando
`SQLITE_DB_PATH` para a cópia preservada e restaurando o bind mount somente
nesse ambiente. Esta versão PostgreSQL não reativa SQLite. Dados gravados
somente no PostgreSQL após o cutover não são sincronizados automaticamente de
volta para SQLite; o rollback é temporário e não constitui dual-write.

## Finalização pelo histórico da DigiSac

As revisions `0013_conversation_cycles` e `0014_retry_scheduling` introduzem
ciclos persistentes e agendamento durável de retries. Com
`DIGISAC_HISTORY_FINALIZATION_ENABLED=true`, webhooks de texto deixam de
alimentar `buffer:{conversation_id}`; áudio e imagem continuam sendo
reservados antecipadamente nas tabelas e filas existentes. No fechamento, a
API persiste o ciclo antes de publicar o job.

O worker move o job de `ia_queue` para `ia_processing` com `LMOVE`, adquire um
lease no PostgreSQL, recupera todas as páginas de
`GET /api/v1/messages?where[ticketId]=...`, deduplica e ordena o histórico,
reconcilia mídias por `message_id` e persiste snapshot e contextos. Mídia em
`pending`/`processing` agenda a retomada no `next_attempt_at` persistido.
Imagem com falha terminal move o ciclo para `media_blocked`, sem classificar
contexto incompleto; o ciclo volta automaticamente quando a imagem é
reprocessada. Falha terminal de áudio continua gerando marcador e
`completed_with_warnings`. O reconciliador usa claims PostgreSQL para impedir
republicações concorrentes entre PostgreSQL e Redis.

Reaberturas criam uma nova sequência. A unicidade do evento e a chave de
classificação `ia:cycle:{cycle_id}` tornam o fechamento e a classificação
idempotentes, enquanto `conversation_cycle_messages.message_id` impede que uma
mensagem seja atribuída a dois ciclos. Se não houver abertura observada, o
início é inferido do ticket, da classificação anterior ou da primeira mensagem
disponível; a estratégia fica registrada no snapshot.

Contextos acima de `IA_CONTEXT_SAFE_INPUT_TOKENS` são divididos sem cortar
mensagens sempre que possível. Cada bloco é resumido, com seu intervalo de IDs
persistido, e somente o contexto reduzido é enviado à classificação final.
URLs de mídia, tokens e binários não são persistidos.

Endpoints compatíveis retornam o ciclo mais recente:

- `GET /ia/status/{conversation_id}`
- `GET /ia/result/{conversation_id}`

Os endpoints de auditoria são:

- `GET /conversations/{conversation_id}/cycles`
- `GET /cycles/{cycle_id}/status`
- `GET /cycles/{cycle_id}/result`

### Configuração e rollout

As variáveis do novo fluxo são:

```dotenv
DIGISAC_HISTORY_FINALIZATION_ENABLED=false
DIGISAC_HISTORY_INITIAL_DELAY_SECONDS=2
DIGISAC_HISTORY_REQUEST_TIMEOUT_SECONDS=15
DIGISAC_HISTORY_MAX_ATTEMPTS=3
DIGISAC_HISTORY_RETRY_BASE_SECONDS=2
FINALIZATION_RECONCILE_INTERVAL_SECONDS=5
FINALIZATION_LEASE_SECONDS=300
MEDIA_STATUS_RECHECK_SECONDS=30
CONTENT_RECONCILE_INTERVAL_SECONDS=5
IA_RETRY_BASE_SECONDS=2
IA_RETRY_MAX_DELAY_SECONDS=900
IA_RETRY_PROVIDER_MARGIN_SECONDS=1
QUOTED_MESSAGE_MAX_CHARS=240
IA_CONTEXT_SAFE_INPUT_TOKENS=96000
IA_CONTEXT_CHUNK_TOKENS=12000
IA_CONTEXT_SUMMARY_OUTPUT_TOKENS=1200
```

Faça o rollout sem janela de perda:

```bash
docker compose build
docker compose up -d postgres redis
docker compose run --rm migrate alembic upgrade head
docker compose up -d api audio_worker image_worker ia_worker
```

Mantenha o flag em `false` durante a migration e a atualização de todos os
containers. Depois de validar acesso e paginação da DigiSac, altere-o para
`true` e recrie API e `ia_worker`:

```bash
docker compose up -d --force-recreate api ia_worker
docker compose ps
docker compose logs --tail=200 api ia_worker audio_worker image_worker
```

O downgrade é permitido somente enquanto não houver ciclos persistidos:

```bash
docker compose run --rm migrate alembic downgrade 0012_validate_event_fk
```

Depois da ativação, rollback de aplicação deve preservar a tabela de auditoria;
não force o downgrade com ciclos existentes.
