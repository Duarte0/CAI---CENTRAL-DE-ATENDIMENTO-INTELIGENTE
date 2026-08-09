# CAI — Analisador de conversas DigiSac

O CAI recebe webhooks da DigiSac, reconstrói a conversa de cada ticket, enriquece
áudios e imagens com IA e gera uma classificação estruturada da intenção do
cliente. O resultado e todo o estado operacional relevante ficam auditáveis no
PostgreSQL.

O sistema **não abre chamados, não responde ao cliente e não transfere tickets**.
Ele termina na classificação e na disponibilização do resultado por API.

## O que o projeto faz

- recebe eventos `ticket.created`, `ticket.updated`, `message.created` e
  `message.updated` da DigiSac;
- valida e normaliza envelopes de webhook, ignora mensagens de bot e deduplica
  eventos;
- registra, em ordem cronológica, os departamentos e atendentes pelos quais o
  ticket passou;
- transcreve áudios e extrai o conteúdo visível de imagens em workers separados;
- finaliza uma conversa quando o ticket é fechado, recuperando o histórico
  completo da DigiSac por um ciclo persistente;
- classifica a intenção do cliente com a API da Groq;
- persiste classificações, mensagens associadas, mídias, ciclos e snapshots no
  PostgreSQL;
- expõe saúde, filas, status e resultados para operação e auditoria.

O contrato produzido pela IA contém apenas:

```json
{
  "intent_type": "question",
  "confidence": 0.98,
  "title": "Erro ao emitir nota fiscal",
  "description": "O cliente informa que não consegue emitir notas fiscais."
}
```

`department` e `agent` são derivados pelo código a partir do histórico DigiSac,
sem participação do modelo. `protocol`, `display_title`, IDs e metadados de ciclo
também são acrescentados pela aplicação.

## Arquitetura

```text
DigiSac
  │
  ├─ ticket.created / ticket.updated
  │    ├─ histórico de departamento e atendente → PostgreSQL
  │    └─ fechamento → ciclo persistente → ia_queue
  │
  └─ message.created / message.updated
       ├─ texto/documento → ciclo persistente
       ├─ áudio → reserva PostgreSQL → audio_transcription_queue
       └─ imagem → reserva PostgreSQL → image_extraction_queue

audio_worker ── download DigiSac + Groq Whisper ──→ PostgreSQL
image_worker ── download DigiSac + Groq Vision  ──→ PostgreSQL

ia_worker
  ├─ recupera e normaliza o histórico do ticket
  ├─ reconcilia transcrições e extrações de imagem
  ├─ aguarda/reagenda mídias pendentes de forma durável
  ├─ divide e resume contextos acima do limite seguro
  ├─ classifica a intenção do cliente com Groq
  └─ grava classificação, snapshot e estado durável no PostgreSQL; publica
     apenas status/resultados transitórios compatíveis no Redis
```

### Componentes

| Componente | Responsabilidade |
| --- | --- |
| `api` | FastAPI, autenticação do webhook, normalização, reservas de mídia, abertura/fechamento de ciclos e consultas. |
| `ia_worker` | Finalização da conversa, montagem do contexto, classificação e reconciliação de ciclos. |
| `audio_worker` | Download de áudio da DigiSac, transcrição e retries. Requer `ffmpeg`. |
| `image_worker` | Download de imagem, análise multimodal e retries. |
| `postgres` | Fonte durável de classificações, mídias, atribuições, diretório DigiSac e ciclos. |
| `redis` | Filas, locks, idempotência temporária, status e resultados com TTL. |
| `migrate` | Aplica a revisão Alembic configurada antes de liberar API e workers. |

RabbitMQ não é usado. PostgreSQL é a fonte de verdade operacional; Redis é uma
camada transitória de transporte e coordenação.

## Finalização persistente por histórico DigiSac

Cada abertura/reabertura cria uma sequência e cada fechamento persiste um ciclo
antes de publicá-lo. O worker consulta todas as páginas de
`GET /api/v1/messages?where[ticketId]=...`, deduplica e ordena as mensagens,
filtra conteúdo fora do ciclo e salva o snapshot usado na análise.

Os estados do ciclo incluem, entre outros, espera por histórico, espera por
mídia, bloqueio por imagem, classificação, conclusão, conclusão com avisos e
falha. `next_attempt_at`, leases e claims no PostgreSQL permitem recuperar jobs
mesmo se houver queda entre a persistência e a publicação no Redis.

- mídia pendente mantém o ciclo em espera até o horário real de nova tentativa;
- falha terminal de imagem bloqueia a classificação para não concluir sem a
  imagem;
- falha terminal de áudio preserva um marcador e pode concluir com avisos;
- uma extração reprocessada desperta automaticamente ciclos bloqueados;
- uma mensagem só pode pertencer a um ciclo;
- a identidade persistida do ciclo e da classificação torna a análise idempotente.

## Tratamento do contexto

São aceitos texto, documento, áudio/voz/PTT e imagem. Mensagens de cliente e
atendente permanecem em ordem cronológica e são renderizadas com identificação
do autor; falas do atendente servem apenas como contexto para classificar a
intenção do cliente. Mensagens de bot são excluídas.

Respostas citadas são representadas por excertos limitados. URLs, tokens e
binários de mídia não são persistidos no snapshot. Se o contexto ultrapassar
`IA_CONTEXT_SAFE_INPUT_TOKENS`, ele é dividido sem cortar mensagens sempre que
possível, resumido por blocos e só então enviado à classificação final.

As categorias de `intent_type` são definidas em `src/core/intents.py`.

## Persistência

O schema pertence ao Alembic; a aplicação não cria nem altera tabelas em tempo
de execução. As principais estruturas são:

- `ia_classifications` e `classification_messages`: resultado e mensagens que
  fundamentaram cada classificação;
- `message_transcriptions` e `message_image_extractions`: reservas, estado,
  retries e conteúdo extraído;
- `ticket_assignment_history` e `ticket_assignment_event_keys`: histórico e
  idempotência das atribuições;
- `digisac_departments`, `digisac_users` e
  `digisac_directory_sync_state`: diretório local para resolução de nomes;
- `conversation_processing_cycles` e `conversation_cycle_messages`: estado,
  snapshot, leases, agendamento e auditoria da finalização persistente.

Classificações recebem um `public_id` UUIDv7. Campos de listas e snapshots usam
JSONB, e timestamps duráveis usam `TIMESTAMPTZ`. Não há exclusão automática:
retenção ou arquivamento devem ser definidos como política operacional explícita.

## Garantias importantes

- O webhook pode exigir HMAC-SHA256 sobre o corpo bruto por meio de
  `X-Digisac-Signature` (`<hash>` ou `sha256=<hash>`).
- Reservas de mídia acontecem antes da marcação de idempotência, permitindo que
  uma entrega DigiSac repetida recupere uma publicação que falhou.
- Filas de mídia e ciclos usam reservas/claims persistentes para evitar
  publicações concorrentes e recuperar trabalho abandonado.
- Retries transitórios respeitam `Retry-After`, backoff e limites configurados.
- O histórico de atribuições nunca inventa transferências. IDs desconhecidos são
  preservados e os nomes só vêm dos endpoints de departamentos e usuários.
- O protocolo não entra no prompt. `display_title` é calculado como
  `[{protocol}] - {title}` quando há protocolo.

## Requisitos

- Python 3.11+
- PostgreSQL 16
- Redis 7
- `ffmpeg` para o worker de áudio
- `GROQ_API_KEY` para os três workers
- `DIGISAC_API_KEY` para downloads, sincronização de diretório e recuperação do
  histórico no fluxo persistente

## Configuração

Crie o arquivo local:

```bash
cp .env.example .env
```

Variáveis principais:

| Variável | Uso | Padrão |
| --- | --- | --- |
| `GROQ_API_KEY` | Classificação, transcrição e visão. | vazio |
| `DIGISAC_API_KEY` | API da DigiSac. | vazio |
| `DIGISAC_API_BASE_URL` | Base da API DigiSac. | `https://inov.digisac.chat/api/v1` |
| `WEBHOOK_SECRET` | Habilita assinatura HMAC do webhook. | vazio |
| `DATABASE_URL` | PostgreSQL da aplicação e Alembic. | obrigatório em execução |
| `REDIS_URL` | Redis de filas e coordenação. | `redis://localhost:6379` |
| `MODEL_NAME` | Modelo de classificação Groq. | `openai/gpt-oss-120b` |
| `AUDIO_TRANSCRIPTION_MODEL` | Modelo de transcrição. | `whisper-large-v3-turbo` |
| `IMAGE_VISION_MODEL` | Modelo multimodal. | `qwen/qwen3.6-27b` |
| `IMAGE_VISION_MAX_COMPLETION_TOKENS` | Orçamento inicial da resposta visual. | `5000` |
| `MAX_TOKENS` | Orçamento solicitado à classificação; o worker impõe mínimo efetivo de `1000`. | `500` no `.env.example`; `3000` se ausente |
| `PROMPT_VERSION` | Identificador do prompt persistido. | `v4` |
| `MAX_RETRY_ATTEMPTS` | Limite de tentativas definitivas. | `3` |
| `RESULT_TTL_SECONDS` | TTL de status/resultados transitórios no Redis. | `86400` |
| `CONTENT_EXTRACTION_WAIT_SECONDS` | Espera compartilhada por mídia. | `30` |
| `CONTENT_RECOVERY_LEASE_SECONDS` | Lease de recuperação de mídia. | `300` |
| `CONTENT_RECONCILE_INTERVAL_SECONDS` | Intervalo do reconciliador de mídia. | `5` |
| `FINALIZATION_RECONCILE_INTERVAL_SECONDS` | Intervalo do reconciliador de ciclos. | `5` |
| `FINALIZATION_LEASE_SECONDS` | Lease de processamento de ciclo. | `300` |
| `MEDIA_STATUS_RECHECK_SECONDS` | Rechecagem de mídia aguardada. | `30` |
| `IA_CONTEXT_SAFE_INPUT_TOKENS` | Limite antes da redução hierárquica. | `96000` |
| `IA_CONTEXT_CHUNK_TOKENS` | Tamanho-alvo de cada bloco. | `12000` |
| `IA_CONTEXT_SUMMARY_OUTPUT_TOKENS` | Saída máxima de cada resumo. | `1200` |

`.env.example` contém a relação operacional completa, incluindo pool e timeouts
do PostgreSQL, retries dos provedores, sincronização do diretório, limites de
imagem e parâmetros de histórico.

## Execução com Docker Compose

Defina ao menos `POSTGRES_PASSWORD` e as chaves exigidas pelo fluxo escolhido:

```bash
cp .env.example .env
docker compose -p cai up --build -d
docker compose -p cai ps
docker compose -p cai logs -f api ia_worker audio_worker image_worker
```

O Compose inicia PostgreSQL, Redis, aplica `alembic upgrade head` no serviço
`migrate` e só então libera API e workers. A API fica em
`http://localhost:8000`; PostgreSQL e Redis também são publicados localmente no
Compose atual.

O fluxo persistente é iniciado diretamente após a aplicação das migrations; não
há flag de finalização nem uma segunda fila de compatibilidade para alternar.

## Execução local

Com PostgreSQL e Redis acessíveis por `DATABASE_URL` e `REDIS_URL`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

DATABASE_URL="$DATABASE_URL" alembic upgrade head
uvicorn main:app --reload
python -m src.workers.ia_worker
python -m src.workers.audio_worker
python -m src.workers.image_worker
```

Cada worker deve rodar em seu próprio processo.

## API

| Método | Rota | Descrição |
| --- | --- | --- |
| `POST` | `/webhook/digisac` | Recebe eventos DigiSac; normalmente responde `202`. |
| `POST` | `/webhook/debug` | Normaliza e devolve um payload sem escrever ou enfileirar. Protegido pelo mesmo HMAC, quando ativo. |
| `GET` | `/health` | Verifica Redis e PostgreSQL. |
| `GET` | `/queues` | Contadores das filas, dead-letters e ciclos por estado. |
| `GET` | `/conversations/{id}/status` | Estado do ciclo mais recente ou do fluxo legado. |
| `GET` | `/conversations/{id}/result` | Resultado mais recente disponível. |
| `GET` | `/conversations/{id}/cycles` | Lista ciclos persistidos da conversa. |
| `GET` | `/cycles/{cycle_id}/status` | Snapshot e estado auditável de um ciclo. |
| `GET` | `/cycles/{cycle_id}/result` | Resultado associado a um ciclo específico. |

As consultas estão sem prefixo de versão no código atualmente montado. A
política de compatibilidade para futuras mudanças incompatíveis continua sendo
registrada nas especificações, mas `/v1/` ainda não é uma rota montada neste
checkout.

O formato real do webhook é o envelope DigiSac. Um exemplo mínimo de mensagem:

```json
{
  "event": "message.created",
  "data": {
    "id": "message-id",
    "ticketId": "ticket-id",
    "type": "chat",
    "text": "Não consigo emitir a nota fiscal",
    "isFromMe": false,
    "isFromBot": false
  }
}
```

O adaptador aceita variações conhecidas do envelope e é a referência para a
normalização dos campos.

## Migrations e evolução de bancos existentes

Para uma instalação nova:

```bash
DATABASE_URL="$DATABASE_URL" alembic upgrade head
```

Instalações antigas ainda anteriores à normalização de identidade não devem
saltar cegamente para `head`. Use a expansão e os backfills reiniciáveis:

```bash
# 1. Expanda schema e índices.
DATABASE_URL="$DATABASE_URL" alembic upgrade 0009_recovery_indexes

# 2. Faça auditoria e dry-run dos backfills.
PYTHONPATH=/app python -m src.utils.audit_postgres --database-url "$DATABASE_URL"
PYTHONPATH=/app python -m src.utils.backfill_classification_public_ids \
  --database-url "$DATABASE_URL"
PYTHONPATH=/app python -m src.utils.backfill_classification_messages \
  --database-url "$DATABASE_URL"

# 3. Aplique, repita os dry-runs até zero pendências e finalize.
PYTHONPATH=/app python -m src.utils.backfill_classification_public_ids \
  --database-url "$DATABASE_URL" --apply
PYTHONPATH=/app python -m src.utils.backfill_classification_messages \
  --database-url "$DATABASE_URL" --apply
DATABASE_URL="$DATABASE_URL" alembic upgrade head
```

As migrations protegem dados preenchidos e algumas contrações são recusadas de
propósito. Não force downgrade de tabelas com classificações ou ciclos. A revisão
`0013_conversation_cycles` só pode ser removida enquanto não houver ciclos
persistidos.

Os arquivos em `migrations/` documentam o SQLite legado; **não** são a autoridade
do schema atual.

### Migração do SQLite legado

Preserve o arquivo original e use um PostgreSQL vazio:

```bash
mkdir -p migration-reports
PYTHONPATH=/app python -m src.utils.migrate_sqlite_to_postgres \
  --sqlite-path data/ia_history.db \
  --database-url "$DATABASE_URL" \
  --report migration-reports/cai-migration-report.json
```

O migrador abre o SQLite somente para leitura, recusa destino já populado,
valida o conteúdo e faz rollback completo em caso de erro. Resultados históricos
que ainda estejam no Redis podem ser importados de forma idempotente com:

```bash
docker compose -p cai exec ia_worker python -m src.utils.backfill_redis_history
```

## Testes e validação

Testes offline:

```bash
PYTHONPATH=/app pytest -q --ignore=tests/test_webhook_local.py
python -m compileall -q src tests alembic
npx --yes pyright
```

O modo persistente é o único caminho suportado e não depende de uma flag no
`.env`. Em 2026-08-09, a execução produziu **118 passed, 33 skipped**; os skips
exigem `CAI_TEST_DATABASE_URL` e não comprovam o schema ou o runtime PostgreSQL.
`tests/test_webhook_local.py`, quando presente, é um teste contra uma API local
real e só deve ser incluído se ela estiver em execução.

Verificação canônica completa, incluindo PostgreSQL descartável:

```bash
PYTHONPATH=/app python scripts/verify.py
```

O runner executa compileall, Pyright estrito, a suíte offline com o modo
persistent explicitamente ativo e, em seguida, todos os testes marcados
`postgres`. Ele cria um projeto Compose com nome único, PostgreSQL 16 em
armazenamento temporário e porta de host publicada dinamicamente; nunca usa a
porta fixa `5433`, `DATABASE_URL` ou `CAI_TEST_DATABASE_URL` do ambiente do
desenvolvedor. Antes dos testes PostgreSQL, o mesmo processo comprova o acesso
ao destino, aplica e verifica Alembic `0014_retry_scheduling` e só então
fornece `CAI_TEST_DATABASE_URL` e `DATABASE_URL` ao subprocesso de testes.

Em um host com acesso à porta publicada, a URL usa `127.0.0.1` e a porta
dinâmica retornada por Compose. Quando o runner está dentro de um container
sem acesso ao loopback do host, ele conecta somente o container do próprio
runner à rede Compose temporária e usa `postgres-test:5432`; essa conexão
também é verificada pelo processo de teste. O projeto, rede e armazenamento
temporários são removidos mesmo quando uma etapa falha. Os resultados offline
e PostgreSQL são reportados separadamente; `test_webhook_local.py` permanece
fora da execução canônica.

Na execução observada do runner após a verificação operacional, a etapa
PostgreSQL produziu **33 passed, 118 deselected**. Esses testes cobrem, no
destino descartável, claim/lease de ciclos, publicação concorrente e sua
liberação após falha, agenda futura, recuperação de áudio/imagem sem duplicar
fila e o despertar somente dos ciclos dependentes de uma imagem recuperada.
Isso não é evidência de Redis, fornecedores, réplicas ou produção.

`POST /webhook/debug` é uma superfície interna de diagnóstico: usa a mesma
validação HMAC quando `WEBHOOK_SECRET` está configurado, não escreve nem
enfileira trabalho e devolve `raw_payload` somente na resposta de diagnóstico.
Esse corpo bruto não deve ser copiado para logs, snapshots, filas ou consultas.
`src/api/debug_routes.py` contém um handler não montado e não anunciado; ele não
é um contrato operacional.

## Operação

Inspeção básica:

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/queues
docker compose -p cai logs --tail=200 api ia_worker audio_worker image_worker
```

Dead-letters indicam trabalho que exige diagnóstico. Reprocessamentos devem ser
restritos ao ID afetado: reserve um item, evite duplicá-lo na fila, confirme o
estado `completed` persistido e só então remova a dead-letter correspondente.

Backup:

```bash
mkdir -p backups
docker compose -p cai exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > backups/cai-$(date +%Y%m%d-%H%M%S).dump
```

Restauração é destrutiva para o destino; faça-a somente em uma janela planejada,
depois de validar o arquivo e o banco alvo.

Auditoria periódica:

```bash
PYTHONPATH=/app python -m src.utils.audit_postgres --database-url "$DATABASE_URL"
```

Monitore crescimento das tabelas, dead-letters, ciclos em espera/bloqueados,
sessões `idle in transaction`, autovacuum e constraints ainda não validadas.
