# CAI — Analisador de conversas DigiSac

O CAI recebe webhooks da DigiSac, reconstrói a conversa de cada ticket, enriquece
áudios e imagens com IA e gera uma classificação estruturada da intenção do
cliente. O resultado e todo o estado operacional relevante ficam auditáveis no
PostgreSQL.

No runtime atual, o sistema **não abre chamados, não responde ao cliente e não
transfere tickets**. Ele termina na classificação e na disponibilização do
resultado por API.

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
- expõe saúde, filas, status e resultados para operação e auditoria;
- expõe uma API administrativa interna, somente leitura e autenticada, para
  triagem de vínculos de identidade Acessórias.

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
  │    └─ fechamento → ciclo persistente no PostgreSQL
  │
  └─ message.created / message.updated
       ├─ texto/documento → ciclo persistente
       ├─ áudio → reserva + polling PostgreSQL → audio_worker
       └─ imagem → reserva + polling PostgreSQL → image_worker

audio_worker ── download DigiSac + Groq Whisper ──→ PostgreSQL
image_worker ── download DigiSac + Groq Vision  ──→ PostgreSQL

ia_worker
  ├─ consulta e reclama ciclos elegíveis diretamente no PostgreSQL
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
| `ia_worker` | Polling/lease PostgreSQL de finalização, montagem do contexto, classificação e despertar seletivo de ciclos bloqueados por mídia. |
| `audio_worker` | Download de áudio da DigiSac, transcrição e retries. Requer `ffmpeg`. |
| `image_worker` | Download de imagem, análise multimodal e retries. |
| `postgres` | Fonte durável de classificações, mídias, atribuições, diretório DigiSac e ciclos. |
| `redis` | Idempotência temporária, status e resultados com TTL e fluxos que ainda têm contrato Redis; as listas de trabalho IA/áudio/imagem são apenas resíduo de cutover e não transportam trabalho ativo. |
| `migrate` | Aplica a revisão Alembic configurada antes de liberar API e workers. |
| `maintenance` | Perfil separado para inventariar e retirar, com relatório e confirmação, somente as listas Redis legadas validadas; não participa do runtime da aplicação. |

RabbitMQ não é usado. PostgreSQL é a fonte de verdade operacional; Redis é uma
camada transitória de transporte e coordenação.

O fechamento de ticket não publica `ia_queue`: o `ia_worker` reclama um ciclo
elegível por lease diretamente no PostgreSQL. `GET /queues` expõe `ia_due`,
`ia_scheduled` e `ia_leased` como métricas duráveis; os contadores
`ia_queue`/`ia_dead_letter` permanecem somente como visibilidade de resíduos de
cutover. Use `python -m scripts.retire_legacy_ia_queue` primeiro em dry run; o
modo `--apply` exige confirmação explícita e nunca remove entradas malformadas
ou sem ciclo PostgreSQL correspondente.

Falhas transitórias de áudio permanecem `pending` com `next_attempt_at` e
backoff independente do limite `MAX_RETRY_ATTEMPTS` da classificação IA. O
`audio_worker` reclama linhas diretamente de `message_transcriptions` com
`FOR UPDATE SKIP LOCKED`, owner e lease; não consome nem republica
`audio_transcription_queue` e não cria dead-letter Redis. O script de cutover
importa dead-letters antigas somente quando o erro persistido é demonstravelmente
transitório. Erros persistidos e logs usam apenas categorias seguras, sem corpo
do provider ou URL assinada.

O mesmo contrato vale para imagens: `image_worker` reclama linhas due de
`message_image_extractions` com `FOR UPDATE SKIP LOCKED`, owner e lease. Retry
transitório permanece em `pending` com `next_attempt_at`; falha permanente fica
em `failed` no PostgreSQL, sem `RPUSH`, `LPOP`, `LRANGE`, `LREM` ou dead-letter
Redis no caminho ativo. As listas legadas são apenas evidência para o comando
bounded `scripts.retire_legacy_image_queue`.

A retirada conjunta deve usar o perfil Compose `maintenance` e
`scripts.retire_validated_legacy_redis_queues`. O comando guarda apenas
digests dos valores Redis e exige um relatório dry-run completo, a revisão do
checkout, um recovery point PostgreSQL, uma segunda fotografia e confirmação
exata por família. A imagem `api` não contém os scripts de manutenção.

## Integração Acessórias aprovada

Os issues 0012–0016 implementaram localmente a fundação do diretório durável
da Acessórias, a identidade mínima de contato DigiSac, o full backfill, a
resolução conservadora de identidade e o mapeamento departamental por IDs
estáveis. O contato é persistido
por `contact.id`, snapshots de ticket são reconciliados no PostgreSQL e
referências `contactId` de mensagens apenas registram hydration individual
deduplicada para execução posterior. O issue 0051 preserva o
`next_attempt_at` futuro quando mensagens repetem a referência: o poller
continua dono da tentativa devida, sem fila Redis ou retry imediato forçado. A
resolução preserva evidência
fingerprintada, vínculos muitos-para-muitos, transições auditáveis e resultado
imutável por ciclo; confirmação continua exclusivamente manual.

O issue 0038 implementa o primeiro slice administrativo da SPEC-0012: as rotas
`GET /admin/acessorias/identity-links`,
`GET /admin/acessorias/contacts/{digisac_contact_external_id}/identity` e
`GET /admin/acessorias/companies` exigem `Authorization: Bearer` com o segredo
`ADMIN_API_TOKEN`. Elas leem apenas projeções PostgreSQL paginadas, retornam IDs,
estados, nomes de exibição e resumos de evidência sem valores de telefone/email,
e nunca executam discovery, hydration, sync, Redis, Request ou providers. O
serviço falha no startup se o segredo não estiver configurado; provisionamento
em secret manager e restrição de rede continuam pré-requisitos de deploy.

O issue 0039 acrescenta os comandos autenticados de confirmação e rejeição:
`POST /admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/confirm`
e `POST /admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/{acessorias_company_external_id}/reject`.
Eles validam somente o par indicado, preservam evidências/transições, derivam
`admin`/`admin_api` do contexto do servidor e usam a ledger PostgreSQL
`identity_admin_commands` para replays idempotentes. Não executam discovery,
providers, Redis, mapeamento, Request ou alterações de resolução histórica.

O issue 0040 acrescenta `POST
/admin/acessorias/contacts/{digisac_contact_external_id}/identity-discovery`.
Ele reutiliza a ledger PostgreSQL com o escopo `identity_discovery` e migration
aditiva `0022_identity_discovery_command`, executa somente a descoberta
determinística sobre fatos locais e retorna IDs externos, estado, links seguros,
contagens e timestamp. Replays não executam discovery novamente; a operação não
chama providers, Redis, hydration, sync, backfill, mapping, Request nem altera
resoluções históricas.

O issue 0026 completa a fronteira de preparação: cada ciclo carrega somente o
`data.contact.id` canônico do snapshot de ticket, resolve a identidade, avalia
o mapping dentro dos limites persistidos e só então avalia a operação Request.
Contato de mensagem, grupo, nome, telefone ou qualquer fallback não substitui
essa proveniência. Identidade ou mapping bloqueado permanece observável e não
faz POST.

Essa fundação não adiciona endpoints públicos nem altera o contrato da IA. A
etapa implementada pelos issues 0017–0019, 0021–0022, 0026 e 0047 cria Requests
internos somente após fatos duráveis de ciclo, classificação, identidade
confirmada, confidence aceita e mapping válido; o efeito no provider é separado
por uma operação PostgreSQL única por ciclo, com
`SolID`, claims, retry conservador somente quando a fronteira prova o pré-envio,
e reconciliação `manual_db`. O payload persistido é carregado e validado antes do
marcador `post_started_at`; falhas nessa etapa ficam retryable sem chamar o
provider. A implementação separa o transporte externo em
`src/core/acessorias_request_provider.py`; a operação PostgreSQL, os claims,
reconciliação e a orquestração continuam em `src/core/acessorias_requests.py`,
que reexporta os contratos do provider para manter os imports existentes. Os
adapters de Request no mesmo processo usam a fronteira neutra
`src/core/provider_coordination.py` para compartilhar o limite Sliding Window
por endpoint/configuração antes do POST, sem persistir token, header ou
payload. Uma `ConnectionError`, timeout ou falha de
protocolo comum permanece ambígua e não inicia um segundo POST. Uma resposta
`429` sem prova documentada de não criação também exige reconciliação e ignora
`Retry-After` como autorização para novo POST. Telefone, nome,
`idFromService`, `jidId`,
`lidId` e grupos permanecem apenas metadata/evidência; não há matching ou
confirmação automática. O full backfill interno de Contacts valida a resposta
de página única ou o fallback `page=N`, deduplica por `contact.id` e publica
somente um snapshot completo em uma transação PostgreSQL; não cria rota pública
nem autoridade de diretório no Redis.

A confidence persistida permanece na escala `0..1`; antes de qualquer POST ela é
normalizada para `0..10` (`confidence * 10`) e exige no mínimo `5.0`, equivalente
a `0.50`. O valor de fronteira é aceito; valor baixo ou inválido bloqueia de
forma durável e sanitizada, sem tentativa, `post_started_at` ou `SolID`. Toda
abertura automática no provider usa `tipo=I` e é sempre interna; não existe
override ou fallback para `tipo=E`. Operações históricas que já iniciaram POST
não são reescritas nem convertidas.

### Reconciliação manual de Request

Uma operação em `reconciliation_required` só pode ser resolvida no procedimento
controlado `manual_db`: depois de consultar a Acessórias, o operador pode
registrar explicitamente o `SolID` por `reconcile_request_operation`; somente
com prova de ausência remota pode liberar uma nova tentativa por
`release_request_operation`. Ambas as operações exigem uma chave idempotente,
motivo seguro, timestamp durável e ator opcional; nenhum usuário da conversa,
token, corpo do provider ou conteúdo da classificação é usado como ator ou
evidência.

## Finalização persistente por histórico DigiSac

Cada abertura/reabertura cria uma sequência e cada fechamento persiste um ciclo
antes de publicá-lo. O worker consulta todas as páginas da API externa da
DigiSac, em `GET /api/v1/messages?where[ticketId]=...`, deduplica e ordena as
mensagens, filtra conteúdo fora do ciclo e salva o snapshot usado na análise.
Esse `/api/v1` pertence à DigiSac e não é uma rota de consulta montada pelo CAI.

Os estados do ciclo incluem, entre outros, espera por histórico, espera por
mídia, bloqueio por mídia, classificação, conclusão, conclusão com avisos e
falha. `next_attempt_at`, leases e claims no PostgreSQL permitem recuperar jobs
sem depender de uma publicação Redis. Áudio é reclamado diretamente por
`audio_worker` e `image_worker`; ambos reclamam mídia diretamente no PostgreSQL.

- mídia pendente mantém o ciclo em espera até o horário real de nova tentativa;
- falha terminal de áudio ou imagem bloqueia a classificação para não concluir
  sem o conteúdo de mídia;
- somente mídia `completed` com texto não vazio torna o ciclo elegível;
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

As categorias de `intent_type` são definidas em `src/core/intents.py`. O contrato
model-facing de classificação — prompts, recuperação/validação do JSON,
normalização e diagnósticos sanitizados — vive em
`src/core/ia_classification.py`, sem dependência de provider, Redis,
PostgreSQL ou estado de ciclo. `src/workers/ia_worker.py` continua responsável
pela chamada Groq, limites/erros do provider, enriquecimento do resultado,
persistência e orquestração do ciclo.

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
- `digisac_contacts` e `digisac_contact_hydrations`: identidade DigiSac por
  `contact.id`, metadata observada e claims/retries duráveis de hydration; uma
  referência repetida não limpa um backoff futuro;
- `conversation_processing_cycles` e `conversation_cycle_messages`: estado,
  snapshot, proveniência canônica do contato, leases, agendamento e auditoria
  da finalização persistente.
- `acessorias_request_operations` e `acessorias_request_reconciliations`:
  operação externa única por ciclo, payload fingerprintado sem conteúdo bruto,
  claims/leases, recuperação de preparação pré-POST, `SolID`, falhas
  sanitizadas e reconciliação administrativa.
- `identity_match_evidence`, `identity_company_links` e
  `identity_company_link_transitions`: fatos duráveis consumidos pela projeção
  administrativa e pelos comandos de confirmação/rejeição.
- `identity_admin_commands`: hashes de chaves, fingerprints e resultados
  sanitizados da idempotência dos comandos administrativos; a chave bruta não é
  persistida nem retornada.
- `digisac_acessorias_reconciliation_executions`: execução manual de dois
  snapshots, hashes, contagens de delta/rematch e falha sanitizada; não guarda
  payload, token ou PII.

O ciclo de vida do pool e a verificação do schema permanecem em
`src/core/db.py`. A persistência de contatos DigiSac e o estado durável de
hydration ficam isolados em `src/core/digisac_contact_repository.py`, usando o
mesmo pool e mantendo a fachada assíncrona compatível para os consumidores
existentes. A persistência de ciclos, membership de mensagens, métricas,
projeção de resultado e despertar seletivo de mídia ficam isolados em
`src/core/conversation_cycle_repository.py`, também com exports compatíveis na
fachada. O histórico idempotente de atribuições e a projeção de nomes de
departamento/agente ficam isolados em
`src/core/ticket_assignment_repository.py`, mantendo os exports compatíveis
da fachada. A persistência durável compartilhada de transcrições e extrações de
imagem fica isolada em `src/core/durable_media_repository.py`, com os exports
de áudio/imagem preservados na fachada para API, workers, utilitários e testes.
A persistência de classificações, vínculos ordenados de mensagens, protocolo e
consultas de existência fica isolada em
`src/core/classification_repository.py`, com os exports compatíveis preservados
em `src/core/db.py`.
O contrato puro de prompt e resposta da classificação fica isolado em
`src/core/ia_classification.py`; o worker mantém somente a orquestração da
chamada Groq e do ciclo.
A persistência do cache de departamentos/usuários DigiSac, do estado de sync e
da resolução de nomes fica isolada em
`src/core/digisac_directory_repository.py`, usando o mesmo pool e preservando
os exports compatíveis da fachada. O sincronizador
`src/core/digisac_directory.py` continua dono da autenticação, paginação,
retry, lock e loop periódico.

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
- A identidade de contato usa somente `contact.id`; hydration individual é
  deduplicada no PostgreSQL, nunca é chamada em linha pelo webhook e nunca
  antecipa `next_attempt_at` em uma referência repetida.
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
| `ADMIN_API_TOKEN` | Bearer da API administrativa interna. | vazio; obrigatório para iniciar a API administrativa |
| `ADMIN_UI_PASSWORD` | Senha única de bootstrap da UI administrativa. | vazio; provisionar em ambiente protegido |
| `ADMIN_SESSION_SECRET` | Chave de assinatura da sessão `HttpOnly` da UI. | vazio; provisionar em ambiente protegido |
| `DATABASE_URL` | PostgreSQL da aplicação e Alembic. | obrigatório em execução |
| `REDIS_URL` | Redis de filas/coordenação dos fluxos que ainda o usam; não é necessário para `audio_worker` ou `image_worker`. | `redis://localhost:6379` |
| `MODEL_NAME` | Modelo de classificação Groq. | `openai/gpt-oss-120b` |
| `AUDIO_TRANSCRIPTION_MODEL` | Modelo de transcrição. | `whisper-large-v3-turbo` |
| `AUDIO_RETRY_BASE_SECONDS` | Backoff inicial de falhas transitórias de áudio. | `2` |
| `AUDIO_RETRY_MAX_DELAY_SECONDS` | Teto do backoff local de áudio. | `900` |
| `AUDIO_RETRY_PROVIDER_MARGIN_SECONDS` | Margem adicionada ao `Retry-After` do provider. | `1` |
| `IMAGE_VISION_MODEL` | Modelo multimodal. | `qwen/qwen3.6-27b` |
| `IMAGE_VISION_MAX_COMPLETION_TOKENS` | Orçamento inicial da resposta visual. | `5000` |
| `MAX_TOKENS` | Orçamento solicitado à classificação; o worker impõe mínimo efetivo de `1000`. | `500` no `.env.example`; `3000` se ausente |
| `PROMPT_VERSION` | Identificador do prompt persistido. | `v4` |
| `MAX_RETRY_ATTEMPTS` | Limite de tentativas definitivas do worker de classificação IA. | `3` |
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
`migrate` e só então libera API e workers. `audio_worker` e `image_worker`
dependem somente de PostgreSQL e da migration; API e IA continuam usando Redis
conforme seus contratos atuais. A API fica em
`http://localhost:8000`; PostgreSQL e Redis também são publicados localmente no
Compose atual.

O fluxo persistente é iniciado diretamente após a aplicação das migrations; não
há flag de finalização nem uma segunda fila de compatibilidade para alternar.

## Execução local

Com PostgreSQL acessível por `DATABASE_URL` (e Redis disponível para API e IA):

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

O backfill completo interno de Contacts pode ser executado após as migrations
com credencial DigiSac configurada:

```bash
PYTHONPATH=/app python -m src.utils.backfill_digisac_contacts
```

A reconciliação manual incremental dos diretórios DigiSac e Acessórias é uma
operação separada, sem scheduler, startup hook, worker loop ou rota HTTP. Ela
inicializa e verifica PostgreSQL, adquire os dois snapshots antes de publicar,
gera um relatório sanitizado e fecha o pool:

```bash
PYTHONPATH=/app python -m src.utils.reconcile_digisac_acessorias --dry-run
PYTHONPATH=/app python -m src.utils.reconcile_digisac_acessorias --apply
```

`--dry-run` é o padrão e não altera as tabelas de negócio; `--apply` é
explícito. A execução usa lock PostgreSQL compartilhado com as publicações de
contatos e do diretório Acessórias, conserva IDs e histórico e, após o commit,
faz uma redescoberta local em lote pelas regras conservadoras de SPEC-0009.
Não usa Redis, o ledger administrativo por contato, Request ou mapping. A
consulta Acessórias usa `ListAll` sem filtro `ativa` e inclui empresas ativas e
inativas; `Status` é preservado e `is_active` é derivado pela normalização
existente. Um apply real continua exigindo autorização operacional
independente.

## API HTTP

A API HTTP local fica disponível em `http://localhost:8000`. O contrato gerado
está publicado em [`/openapi.json`](http://localhost:8000/openapi.json), com
Swagger UI em [`/docs`](http://localhost:8000/docs) e ReDoc em
[`/redoc`](http://localhost:8000/redoc). Esses links documentam as rotas sem
prefixo de versão montadas neste checkout; `/v1/` e `/v2/` são somente política
de compatibilidade futura.

O webhook DigiSac pode exigir HMAC-SHA256 em `X-Digisac-Signature` quando
`WEBHOOK_SECRET` estiver configurado. O digest pode ser hexadecimal ou usar o
formato `sha256=<digest>`. As consultas internas (`/health`, `/queues`, status,
resultados e ciclos) não têm autenticação adicional atualmente. O aceite `202`
do webhook é assíncrono e não significa classificação concluída: estados
intermediários e terminais ficam disponíveis nas rotas de status, enquanto o
resultado só existe quando a classificação persistida está disponível.

Respostas conhecidas usam o corpo `{"detail":"..."}` para erros mapeados,
incluindo `404`, `401` e a indisponibilidade de banco em `/health` (`503`).
Erros de validação de tipos seguem o formato padrão FastAPI (`422`); falhas
não mapeadas de Redis ou servidor não têm um envelope de negócio estável.

| Método | Rota | Descrição |
| --- | --- | --- |
| `POST` | `/webhook/digisac` | Recebe eventos DigiSac; normalmente responde `202`. |
| `GET` | `/health` | Verifica Redis e PostgreSQL. |
| `GET` | `/queues` | Contadores das filas, dead-letters e ciclos por estado. |
| `GET` | `/conversations/{id}/status` | Estado persistente do ciclo mais recente. |
| `GET` | `/conversations/{id}/result` | Resultado mais recente disponível. |
| `GET` | `/conversations/{id}/cycles` | Lista ciclos persistidos da conversa. |
| `GET` | `/cycles/{cycle_id}/status` | Snapshot e estado auditável de um ciclo. |
| `GET` | `/cycles/{cycle_id}/result` | Resultado associado a um ciclo específico. |

As oito rotas acima são a superfície HTTP operacional original e continuam sem
autenticação adicional nas consultas. A administração da identidade Acessórias
é uma superfície interna separada, com seis operações autenticadas por
`Authorization: Bearer` usando `ADMIN_API_TOKEN`:

| Método | Rota | Descrição |
| --- | --- | --- |
| `GET` | `/admin/acessorias/identity-links` | Triagem paginada de vínculos e evidências sanitizadas. |
| `GET` | `/admin/acessorias/contacts/{digisac_contact_external_id}/identity` | Projeção segura de um contato canônico. |
| `GET` | `/admin/acessorias/companies` | Busca paginada de empresas presentes e ativas. |
| `POST` | `/admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/confirm` | Confirmação idempotente de um par explícito. |
| `POST` | `/admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/{acessorias_company_external_id}/reject` | Rejeição idempotente com histórico de auditoria. |
| `POST` | `/admin/acessorias/contacts/{digisac_contact_external_id}/identity-discovery` | Redescoberta determinística sobre fatos locais. |

As seis rotas administrativas retornam somente IDs externos, estados, contagens
e timestamps seguros. PostgreSQL é a autoridade dos comandos: o replay da
mesma chave converge para o mesmo resultado, a reutilização incompatível da
chave retorna conflito e locks por contato impedem transições duplicadas sob
concorrência. Elas não chamam providers ou Redis, não alteram resolução
histórica de ciclos e não criam Requests. Os issues 0042 e 0043 adicionam o
shell local em `/admin/acessorias/ui`, login/logout, sessão `HttpOnly` assinada
e a fila/detalhe/busca read-only da SPEC-0013. Os paths BFF
`/admin/acessorias/ui/api/identity-links`,
`/admin/acessorias/ui/api/contacts/{id}/identity` e
`/admin/acessorias/ui/api/companies` usam somente o cookie de sessão, chamam
as projeções sanitizadas existentes e mantêm o `ADMIN_API_TOKEN` no servidor.
Filtros de fila limitados a `candidate`, `ambiguous` e `unresolved`, cursores
opacos, estados de erro/retry e busca somente de empresas presentes e ativas
ficam no cliente local. Os paths BFF de confirmação, rejeição e discovery usam
o mesmo cookie, razões fixas (`operator_verified` e `operator_rejected`),
chaves idempotentes transitórias e refresh de fila/detalhe após sucesso ou
replay; não chamam providers, Redis ou o fluxo de Request.

As consultas estão montadas sem prefixo de versão. `/v1/` e `/v2/` permanecem
somente como política de compatibilidade futura; não são aliases nem rotas
montadas neste checkout.

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
PYTHONPATH=/app python -m pytest -q
PYTHONPATH=/app python -m pytest --collect-only -q
python -m compileall -q src tests alembic scripts
npx --yes pyright
```

O modo persistente por histórico DigiSac é o único caminho suportado e não
depende de uma flag no `.env`. Em uma execução histórica de 2026-08-20, a
etapa offline produziu **224 passed, 69 skipped** e a etapa PostgreSQL
descartável **69 passed, 224 deselected**; os skips offline exigem
`CAI_TEST_DATABASE_URL` e os resultados não comprovam Redis, fornecedores ou
produção. O baseline atual está registrado abaixo, na execução do issue 0040.

O smoke test live do webhook é opt-in e requer uma API local deliberadamente
iniciada. Ele preserva o payload sintético e o endpoint local existentes:

```bash
PYTHONPATH=/app python tests/test_webhook_local.py
```

Sem uma API em `localhost:8000`, o comando falha de forma visível com erro de
conexão; ele não é executado durante importação, coleta pytest ou pelo runner
canônico. Respostas HTTP não bem-sucedidas também resultam em código de saída
não zero. O smoke test live não é evidência da suíte offline, PostgreSQL
descartável, DigiSac, provedores ou produção.

Verificação canônica completa, incluindo PostgreSQL descartável:

```bash
APP_TIMEZONE=UTC PYTHONPATH=/app python scripts/verify.py
```

O runner executa compileall, Pyright estrito, a suíte offline sem selecionar
qualquer flag de finalização e, em seguida, todos os testes marcados `postgres`.
Ele cria um projeto Compose com nome único, PostgreSQL 16 em
armazenamento temporário e porta de host publicada dinamicamente; nunca usa a
porta fixa `5433`, `DATABASE_URL` ou `CAI_TEST_DATABASE_URL` do ambiente do
desenvolvedor. Antes dos testes PostgreSQL, o mesmo processo comprova o acesso
ao destino, aplica e verifica Alembic head `0024_durable_media_leases` e só então
fornece `CAI_TEST_DATABASE_URL` e `DATABASE_URL` ao subprocesso de testes.

Em um host com acesso à porta publicada, a URL usa `127.0.0.1` e a porta
dinâmica retornada por Compose. Quando o runner está dentro de um container
sem acesso ao loopback do host, ele conecta somente o container do próprio
runner à rede Compose temporária e usa `postgres-test:5432`; essa conexão
também é verificada pelo processo de teste. O projeto, rede e armazenamento
temporários são removidos mesmo quando uma etapa falha. Os resultados offline
e PostgreSQL são reportados separadamente; o smoke test live permanece fora da
execução canônica.

Em uma execução histórica observada do runner em 2026-08-20, a etapa offline produziu
**224 passed, 69 skipped** e a etapa PostgreSQL produziu **69 passed, 224
deselected**. Esses testes cobrem, no
destino descartável, claim/lease de ciclos, publicação concorrente e sua
liberação após falha, agenda futura, recuperação de áudio/imagem sem duplicar
fila, o despertar somente dos ciclos dependentes de uma imagem recuperada, a
fundação do diretório Acessórias, a identidade/hydration de contatos DigiSac,
a resolução conservadora de identidade, o mapeamento departamental com
auditoria e snapshots por ciclo, e a criação durável de Request com retry
seguro, payload pré-POST, claims concorrentes, reconciliação de resultado
incerto e de `429`, `SolID`, admissão compartilhada entre adapters, a ordem de
preparação de identidade/mapping e a recuperação segura de operações
`mapping_missing` sem POST prévio.
Os resultados offline e PostgreSQL são evidência local descartável; não
comprovam disponibilidade de Redis, DigiSac, Groq, réplicas, deployment ou
produção.

Na validação do issue 0038 em 2026-08-21, o runner descartável com
`APP_TIMEZONE=UTC` passou compileall, Pyright, **236 passed, 70 skipped** offline
e **70 passed, 236 deselected** no PostgreSQL 16 descartável. A execução sem
esse override preservou um failure preexistente de timezone em
`tests/test_department_mapping.py`; ele não envolve a API administrativa.

Na validação do issue 0039 em 2026-08-21, o runner descartável com
`APP_TIMEZONE=UTC` passou compileall, Pyright, **237 passed, 74 skipped** offline,
Alembic `0021_identity_admin_commands` e **74 passed, 237 deselected** no
PostgreSQL 16. A evidência é local e descartável; não comprova Redis, providers,
secret manager, deployment ou produção.

Na validação do issue 0040 em 2026-08-21, o runner descartável com
`APP_TIMEZONE=UTC` passou compileall, Pyright, **238 passed, 76 skipped** offline,
Alembic `0022_identity_discovery_command` e **76 passed, 238 deselected** no
PostgreSQL 16. A execução sem o override manteve somente o failure preexistente
de timezone em `tests/test_department_mapping.py`; a falha não envolve o slice
administrativo. A evidência é local e descartável; não comprova Redis,
providers, secret manager, deployment ou produção.

Na validação do issue 0043 em 2026-08-21, o runner descartável com
`APP_TIMEZONE=UTC` passou compileall, Pyright, **249 passed, 76 skipped** offline,
Alembic `0022_identity_discovery_command` e **76 passed, 249 deselected** no
PostgreSQL 16 descartável. A evidência inclui os testes HTTP/BFF e de contrato
da UI; não houve migration nova. A validação é local e descartável e não
comprova Redis, providers, secret manager, deployment ou produção.

Na validação do issue 0044 em 2026-08-22, o runner descartável com
`APP_TIMEZONE=UTC` passou JavaScript syntax check, compileall, Pyright,
**253 passed, 76 skipped** offline, Alembic `0022_identity_discovery_command`
e **76 passed, 253 deselected** no PostgreSQL 16 descartável. A cobertura
focada validou os três paths BFF, razões fixas, replay `201`/`200`, erros
sanitizados, chave transitória no cliente e contrato local sem storage ou
recursos externos. Nenhum browser harness ou executável Playwright estava
disponível neste ambiente; não houve QA renderizado nem aceitação de produção.

Na validação do issue 0045 em 2026-08-25, o runner descartável com
`APP_TIMEZONE=UTC` passou compileall, Pyright, **255 passed, 77 skipped** offline,
Alembic `0023_manual_reconciliation` e **77 passed, 255 deselected** no
PostgreSQL 16. A cobertura inclui dry-run não destrutivo, apply atômico, replay
idempotente, retenção histórica e redescoberta após a publicação. O resultado
é evidência local/descartável: não comprova disponibilidade live do provider
Acessórias, Redis, credenciais, deployment ou produção.

Na validação do issue 0047 em 2026-08-26, o runner descartável passou compileall,
Pyright estrito, **269 passed, 82 skipped** offline, Alembic
`0023_manual_reconciliation` e **82 passed, 269 deselected** no PostgreSQL 16.
A cobertura valida o gate de confidence, a fronteira `0.50` e o payload interno
`tipo=I`. O resultado é evidência local/descartável e não comprova provider live,
Redis, credenciais, deployment ou produção.

Na validação do issue 0050 em 2026-09-02, a suíte PostgreSQL descartável passou
**34 testes**, incluindo claim concorrente de imagem, lease/ownership, mídia
pendente, recuperação de ciclo bloqueado e regressões de transcrição. A suíte
offline completa passou **280 testes, com 84 skips** restritos ao banco não
configurado. A evidência é local/descartável e não comprova provider live,
credenciais, deployment ou produção.

Na validação do issue 0051 em 2026-09-03, compileall, Pyright e o runner
descartável passaram; a suíte offline teve **280 passed, 86 skipped** e a
etapa PostgreSQL teve **86 passed, 280 deselected** com head
`0024_durable_media_leases` (o override `APP_TIMEZONE=UTC` evita o failure
histórico de timezone de `test_department_mapping.py`). Os testes de
identidade/hydration provaram decisões concorrentes durante backoff futuro,
preservação exata de `next_attempt_at`, handoff de falha devida ao poller e
no-op de hydration sucedida/current. Não houve migration nova nem dependência
Redis adicionada; essa evidência continua local/descartável.

Não há uma rota de diagnóstico de webhook. O endpoint de produção é a única
superfície de ingestão; respostas e logs operacionais expõem somente campos
estruturados e motivos sanitizados, nunca o corpo bruto da requisição ou a
resposta bruta/parcial do modelo, título, descrição ou raciocínio da
classificação. A extração normal do webhook retém nos logs somente evento,
presença/tipo e caminho de origem; não registra valores de mensagem/contato,
URLs ou segredos. Os diagnósticos de recuperação do parser Groq retêm somente
outcome e metadados estruturais limitados.

## Operação

Inspeção básica:

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/queues
docker compose -p cai logs --tail=200 api ia_worker audio_worker image_worker
```

Áudio e imagem não possuem mais retry ou dead-letter ativo em Redis: `pending`,
`next_attempt_at`, `processing`, lease, `completed` e `failed` ficam em
`message_transcriptions` e `message_image_extractions`. As seis listas legadas
(`ia_queue`, `ia_dead_letter`, as duas de áudio e as duas de imagem) são
resíduo de cutover. O procedimento suportado usa uma imagem de manutenção
separada, é dry-run por padrão e não apaga itens desconhecidos, malformados ou
dead-letters transitórios.

Prepare um diretório de relatório gravável pelo usuário do container e capture
a revisão do checkout. O valor de `--max-items` deve ser maior que o tamanho
físico observado em todas as listas:

```bash
REPORT_DIR=$(mktemp -d)
export MAINTENANCE_REPORT_DIR="$REPORT_DIR"
REVISION=$(git rev-parse HEAD)
OPERATOR="nome-do-operador"
REPORT_NAME="cai-legacy-queues-dry-run.json"
MAX_ITEMS=50000

docker compose -p cai --profile maintenance run --rm maintenance \
  python -m scripts.retire_validated_legacy_redis_queues \
  --dry-run --operator "$OPERATOR" --revision "$REVISION" \
  --compose-project cai --max-items "$MAX_ITEMS" \
  --api-url http://api:8000 --report "/reports/$REPORT_NAME"
```

Revise e arquive o JSON antes de qualquer apply. Ele contém `/health`,
`/queues`, schema, invariantes PostgreSQL, contagens, estados, duplicidades e
digests SHA-256; não contém valores Redis, mensagens, payloads ou segredos.
Registre também o status dos serviços:

```bash
docker compose -p cai ps api ia_worker audio_worker image_worker
```

Faça o backup ou registre o recovery point aprovado antes de aplicar:

```bash
BACKUP="$REPORT_DIR/cai-$(date -u +%Y%m%d-%H%M%S).dump"
docker compose -p cai exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "$BACKUP"
```

Aplique uma família por vez. O coordenador revalida a segunda fotografia e
recusa crescimento, troca de valores, snapshot truncado, runtime indisponível
ou relatório de outro operador/revisão. As confirmações são deliberadamente
distintas:

```bash
docker compose -p cai --profile maintenance run --rm maintenance \
  python -m scripts.retire_validated_legacy_redis_queues \
  --apply --family ia --confirm retire-legacy-ia-queue \
  --operator "$OPERATOR" --revision "$REVISION" --compose-project cai \
  --max-items "$MAX_ITEMS" --api-url http://api:8000 \
  --backup-reference "$BACKUP" --report "/reports/$REPORT_NAME"

docker compose -p cai --profile maintenance run --rm maintenance \
  python -m scripts.retire_validated_legacy_redis_queues \
  --apply --family image --confirm retire-legacy-image-queue \
  --operator "$OPERATOR" --revision "$REVISION" --compose-project cai \
  --max-items "$MAX_ITEMS" --api-url http://api:8000 \
  --backup-reference "$BACKUP" --report "/reports/$REPORT_NAME"

docker compose -p cai --profile maintenance run --rm maintenance \
  python -m scripts.retire_validated_legacy_redis_queues \
  --apply --family audio --confirm retire-legacy-audio-queue \
  --operator "$OPERATOR" --revision "$REVISION" --compose-project cai \
  --max-items "$MAX_ITEMS" --api-url http://api:8000 \
  --backup-reference "$BACKUP" --report "/reports/$REPORT_NAME"
```

Cada `LREM` remove somente uma ocorrência exata previamente validada. O
comando não faz provider call, republicação, recuperação automática de
dead-letter, escrita PostgreSQL, `FLUSHDB`, `FLUSHALL` ou remoção de
`processed:*`, `ia_status:*`, `ia_result:*`, `ia_processing` e outras famílias.
Após cada família, revise o relatório atualizado; em caso de interrupção,
gere novo dry-run e reavalie o restante antes de continuar.

Resíduos das antigas chaves Redis de buffer/debounce podem ser auditados e
removidos somente por uma operação manual, limitada e revisada. O dry-run
registra famílias, tipos, TTLs, contagens duráveis e apenas digests de chaves;
`ia_processing` permanece retido até haver prova de que não contém trabalho
recuperável:

```bash
PYTHONPATH=/app python -m scripts.redis_residue_cleanup \
  --dry-run --report reports/redis-residue-dry-run-YYYY-MM-DD.json
# revise o relatório antes de executar a allowlist aprovada
PYTHONPATH=/app python -m scripts.redis_residue_cleanup \
  --apply --confirm --report reports/redis-residue-dry-run-YYYY-MM-DD.json
```

O comando só pode apagar as seis famílias explicitamente allowlisted pelo
issue 0037, uma chave por vez; nunca usa `FLUSHDB`, `FLUSHALL` ou um glob
genérico. Ele revalida Redis, filas, PostgreSQL, leases, agendas e marcadores
antes e depois, preservando filas/dead-letters ativos, `processed:*`,
`ia_status:*`, `ia_result:*` e todo o estado durável PostgreSQL.

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
