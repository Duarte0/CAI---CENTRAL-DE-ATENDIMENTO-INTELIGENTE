# Implementation specifications

Baseline de especificações revisada no passe de specs de 2026-08-14. PRD e arquitetura estão presentes como baselines derivados da implementação; código, migrations, configuração e testes continuam prevalecendo para o comportamento atual. As especificações são contratos vinculantes para trabalho futuro e não aprovam políticas de produto ainda abertas.

## Especificações ativas

| ID | Especificação | Status | Prioridade/Fase | Dependências | Resumo |
| --- | --- | --- | --- | --- | --- |
| SPEC-0001 | [Contrato compartilhado de dados e análise](0001-shared-data-and-analysis-contract.md) | Baseline ativo v1.13; finalização IA PostgreSQL no issue 0048; transcrição de áudio e extração de imagem PostgreSQL nos issues 0049–0050; backoff de hydration no issue 0051; retirada validada das listas legadas no issue 0052; idempotência de webhook no issue 0053; views IA Redis sem produtor e sunset bounded no issue 0054; runtime Redis-free e maintenance-only no issue 0055; pré-disposição do storage retido registrada no issue 0056, ainda bloqueada pelo gate 0054; boundaries estruturais 0032, 0033 e 0035; auditoria Redis 0037 | P0 / baseline | — | Fonte durável, integridade, contrato IA, ledger PostgreSQL de webhook, migrações, fronteiras de privacidade, runtime PostgreSQL-only e descarte Redis somente após backup e revisão exatos. |
| SPEC-0002 | [Webhook DigiSac e API de consulta](0002-digisac-webhook-and-query-api.md) | Baseline ativo v2.1; mídia sem publicação Redis ativa nos issues 0049–0050; hydration fora da idempotência de evento no issue 0051; ledger PostgreSQL de webhook no issue 0053; runtime Redis-free, health PostgreSQL-only e `/queues` durável no issue 0055; disposição de storage separada no issue 0056; boundaries estruturais 0030 e 0033 | P0 / baseline | SPEC-0001 | HMAC, normalização, eventos e consultas atualmente sem versão; reservas de áudio/imagem e idempotência persistem diretamente no PostgreSQL; health não consulta Redis e `/queues` não fabrica campos legados. |
| SPEC-0003 | [Finalização durável, contexto e mídia](0003-durable-finalization-and-media.md) | Baseline ativo v2.5; polling/lease PostgreSQL da IA, áudio e imagem nos issues 0048–0050; gate compartilhado de áudio/imagem no issue 0046; retirada validada das listas legadas no issue 0052; views IA Redis sem produtor e sunset bounded no issue 0054; runtime Redis-free no issue 0055; storage retido e sem impacto no estado durável no issue 0056; boundaries estruturais 0029, 0031 e 0035; auditoria Redis 0037 | P0/P1 | SPEC-0001, SPEC-0002 | Ciclo persistente único, contexto, mídia, retry e recuperação concorrente; finalização IA e mídia sem transporte Redis ativo, runtime sem Redis, listas históricas tratadas somente por manutenção bounded, mídia terminal bloqueia e somente conteúdo não vazio concluído habilita classificação. |
| SPEC-0004 | [Baseline reprodutível de testes e verificação](0004-reproducible-verification-baseline.md) | Implementado v2.1; evidência issues 0050–0051 e 0055 em 2026-09-03; gate operacional 0056 separado do runner | P0/P1 | SPEC-0001–0003 | Suíte rastreada, isolamento, runner descartável, smoke de Compose sem Redis e evidência local separada por etapa; não executa comandos destrutivos de infraestrutura. |
| SPEC-0005 | [Reconciliação do baseline documental](0005-documentation-baseline-reconciliation.md) | Implementado v1.4; issue 0041 | P1 / reconciliação documental | SPEC-0002–0004, SPEC-0006, SPEC-0012 | Reconciliou PRD/arquitetura/rastreabilidade com `0022`/`238+76` e a API administrativa, sem alegar UI. |
| SPEC-0006 | [Documentação da API HTTP e contrato OpenAPI](0006-api-documentation-and-openapi-contract.md) | Implementado v1.9 | P1 / documentação de compatibilidade | SPEC-0001–0005, SPEC-0012 | Publica OpenAPI/Swagger/ReDoc para as oito rotas originais e as seis rotas administrativas internas montadas, incluindo idempotência PostgreSQL do webhook, métricas duráveis de áudio e imagem, health PostgreSQL-only, `/queues` sem campos Redis legados e consultas de status/resultado sem as views Redis aposentadas; a disposição de storage 0056 não altera o contrato HTTP. |
| SPEC-0007 | [Fundação do diretório externo Acessórias](0007-acessorias-external-directory-foundation.md) | Implementado localmente v1.2; issues 0012, 0034 e 0045 | P0 / Milestone A | SPEC-0001, SPEC-0004, configuração segura de credencial | Diretório PostgreSQL de empresas, contatos, departamentos e relações, com delta manual seguro; `ListAll` inclui todos os status sem filtro `ativa`; não cria Request nem identidade DigiSac. |
| SPEC-0008 | [Fundação de identidade de contato DigiSac](0008-digisac-contact-identity-foundation.md) | Implementado localmente v1.6; issues 0013, 0014, 0026, 0045 e 0051; boundary estrutural 0028 | P0 / Milestone B | SPEC-0001, SPEC-0002, SPEC-0004, SPEC-0007 | Contato mínimo por `contact.id`, upsert de ticket, provenance canônica do contato no ciclo, hydration com backoff preservado, full backfill e consumo pela reconciliação manual; sem deleção por ausência. |
| SPEC-0009 | [Resolução de identidade DigiSac–Acessórias](0009-digisac-acessorias-identity-resolution.md) | Implementado localmente v1.3; issues 0015, 0026 e 0045 | P1 / Milestone C | SPEC-0001, SPEC-0004, SPEC-0007, SPEC-0008 | Evidência/candidatos conservadores, vínculos muitos-para-muitos, resolução por ciclo e redescoberta manual em lote; confirmação é manual, nunca automática. |
| SPEC-0010 | [Mapeamento de departamento DigiSac para Acessórias](0010-digisac-acessorias-department-mapping.md) | Implementado localmente v1.3; issues 0016, 0020 e 0026; boundary estrutural 0030 | P1 / Milestone D | SPEC-0001, SPEC-0003, SPEC-0007–0009 | Regras globais por IDs externos estáveis, seleção da atribuição dentro dos limites do ciclo, auditoria de lifecycle, snapshots contra `company_departments` e gate após identidade confirmada; não usa IA nem cria Request. |
| SPEC-0011 | [Criação durável de Request Acessórias](0011-durable-acessorias-request-creation.md) | Implementado localmente v1.5; issues 0017–0019, 0021–0022, 0026 e 0047; boundaries estruturais 0034 e 0036 | P1 / Milestone E | SPEC-0001, SPEC-0003, SPEC-0007–0010 | Criação multipart interna (`tipo=I`) durável, gate de confidence `0..10`/mínimo `5.0` (`0..1`/`0.50` persistido), preparação explícita, recuperação somente pré-POST comprovada, limite Sliding Window compartilhado no processo, payload pré-POST validado antes do marcador, sem idempotency key do provider e reconciliação manual de `429` incerto. |
| SPEC-0012 | [Administração de vínculos contato DigiSac–empresa Acessórias](0012-administrative-contact-company-link-management.md) | Implementado localmente v1.2; issues 0038, 0039, 0040 e 0045 | P1 / Milestone C.1 | SPEC-0001, SPEC-0006–0009; `ADMIN_API_TOKEN` em secret manager/ambiente protegido | API interna autenticada para listar, consultar, confirmar, rejeitar e redescobrir um contato; a reconciliação manual em lote é uma fronteira separada e não usa o ledger por contato. |
| SPEC-0013 | [Interface web administrativa para conciliação de identidade](0013-administrative-identity-link-review-ui.md) | Implementado localmente v1.5; issues 0042–0044 | P1 / Milestone C.2 | SPEC-0012; `ADMIN_API_TOKEN`, `ADMIN_UI_PASSWORD` e `ADMIN_SESSION_SECRET` em ambiente protegido | Fundação FastAPI com login/logout, sessão assinada, fila, detalhe, busca e ações de confirmação/rejeição/discovery via BFF local; sem matching no frontend ou acesso direto ao banco. |

A evidência mais recente registrada para o runner de SPEC-0004 é **280 passed,
86 skipped** na etapa offline. A validação PostgreSQL do issue 0051 passou
**86 passed, 280 deselected** com Alembic head `0024_durable_media_leases`.
Na mesma execução, a validação PostgreSQL focada do issue 0050 passou
**34 testes** após aplicar a migration `0024_durable_media_leases`, cobrindo
claim atômico de imagem, agenda futura, lease/ownership, retry, gate de mídia,
admissão por webhook/IA e regressões de áudio/imagem. Essa execução foi
descartável e não substitui evidência de provider ou produção. A execução
anterior de issue 0047, antes da migration 0024, permanece registrada abaixo
como evidência histórica com head `0023_manual_reconciliation`.

Na validação do issue 0051 em 2026-09-03, os testes de identidade/hydration
incluíram referências concorrentes durante backoff futuro, preservação exata de
`next_attempt_at`, handoff de falha devida ao poller e no-op de hydration
sucedida/current. Não houve migration nova; a evidência permanece local e
descartável e não comprova provider, Redis, deployment ou produção.

Na implementação do issue 0052 em 2026-09-03, o contexto suportado passou a ser
o target/profile Docker `maintenance`, separado da imagem `api`. O coordenador
de retirada exige dry-run completo, operador, revisão do checkout, relatório
arquivado, recovery point e apply de uma família por vez. Digests SHA-256
substituem valores Redis no relatório; a segunda fotografia, `/health`,
`/queues`, schema e invariantes PostgreSQL são revalidados antes/depois. A
execução dos testes de filas/coordenador passou **15 testes**; o runner
canônico com `APP_TIMEZONE=UTC` passou **285 testes offline e 86 testes
PostgreSQL**. No runtime `cai`, o apply removeu 17.164 entradas IA, 71 de
imagem e 0 de áudio, e o dry-run final encontrou as seis listas vazias; os
totais duráveis permaneceram preservados. O detalhe e os artefatos estão no
issue 0052.

Na implementação do issue 0053 em 2026-09-03, o digest genérico do webhook
passou a ser decidido e expirado em `webhook_event_keys` no PostgreSQL. A
migration `0025_webhook_event_keys` impõe digest SHA-256 minúsculo, timestamps
`TIMESTAMPTZ`, constraint de expiração e índice de limpeza. A operação é
concorrente e fail-closed; o cleanup é bounded e registra somente contagens. O
handoff de `processed:*` é separado, report-bound e sem exclusão da fonte Redis:
API antiga parada/drenada, dry-run completo revisado, backup/recovery point e
apply antes do início da API nova. O runner canônico passou compileall, Pyright,
**290 passed, 90 skipped** offline e **90 passed, 290 deselected** no PostgreSQL
16 descartável, com head `0025_webhook_event_keys`. No runtime `cai`, o backup
custom-format foi validado, 171 marcadores vivos foram importados, nenhuma chave
Redis foi removida e o health interno retornou `{"status":"ok"}`. A API e os
três workers foram reconstruídos com a revisão `db7a077`; o ledger tinha 176
linhas vivas na verificação final, incluindo cinco novas entregas após o
handoff. Os artefatos e checksums ficam registrados na issue 0053; a evidência
é específica desse runtime e não é uma alegação de produção ampla.

Na implementação do issue 0054 em 2026-09-03, `ia_worker` deixou de depender do
Redis e de criar `ia_status:*`/`ia_result:*`; as rotas públicas mantiveram suas
respostas PostgreSQL. O inventário bounded foi isolado no comando de manutenção
`scripts.retire_ia_redis_compatibility`, que registra somente contagens, buckets
de TTL, digests e matches duráveis. No runtime `cai`, o dry-run encontrou 80
chaves de cada família, com 80 matches duráveis e zero resultado válido sem
correspondência; após 30 segundos as contagens permaneceram 80/80. O importador
histórico permanece no perfil `maintenance`; nenhuma chave foi removida antes da
janela de observação de 86400 segundos e da revisão histórica explícita. A
evidência é específica do checkout/runtime nomeado e não é uma alegação de
produção ampla.

Na implementação do issue 0055 em 2026-09-03, a API e o Compose deixaram de
instalar, inicializar ou exigir Redis. O contrato `/health` passou a verificar
somente PostgreSQL e `/queues` passou a devolver exclusivamente métricas
duráveis, sem os seis campos de listas legadas. O cliente Redis e os scripts
históricos foram movidos para a imagem/profile `maintenance`, com
`MAINTENANCE_REDIS_URL` explícita. Os testes source/Compose/OpenAPI cobrem a
fronteira; o runtime `cai` foi reconstruído com a imagem Redis-free e o health
interno permaneceu `{"status":"ok"}`. O container/volume Redis foi mantido
fora da aplicação para o issue 0056; essa evidência é específica do checkout e
runtime nomeado, não uma alegação de produção ampla.

Na verificação de pré-disposição do issue 0056 em 2026-09-04, o runtime
`cai` permaneceu saudável sem Redis na topologia ativa, e o alvo histórico foi
resolvido de forma exata como `cai-redis-1`/`cai_redis_data`, sem anexos a
PostgreSQL ou workers. A janela de observação do issue 0054 ainda não havia
completado 86400 segundos e não havia backup PostgreSQL final dessa janela
arquivado; por isso nenhum container ou volume foi removido. A disposição
continua sendo uma operação de infraestrutura separada, com validação em alvo
PostgreSQL descartável e sem `prune`, `down -v`, `FLUSHDB` ou `FLUSHALL`.

O issue 0045 acrescenta a migration `0023_manual_reconciliation`
e a fronteira manual incremental. Em 2026-08-25, a execução canônica com
`APP_TIMEZONE=UTC` passou **255 passed, 77 skipped** offline e **77 passed, 255
deselected** no PostgreSQL descartável; seus testes não comprovam disponibilidade
live do provider ou aceitação de produção.

Em 2026-08-26, a execução canônica do issue 0047 passou compileall, Pyright
estrito, **269 passed, 82 skipped** offline, Alembic
`0023_manual_reconciliation` e **82 passed, 269 deselected** no PostgreSQL
descartável. A cobertura inclui o gate `confidence * 10 >= 5.0`, aceitação da
fronteira `0.50`, bloqueio fail-closed e payload interno `tipo=I`; a evidência
continua local/descartável e não comprova provider, credenciais, deployment ou
produção.
A evidência anterior de issue 0036 foi **224 passed, 69 skipped** na etapa
offline e **69 passed, 224 deselected** no PostgreSQL. A evidência anterior de
issue 0033 foi **220 passed,
69 skipped** offline e **69 passed, 220 deselected** no PostgreSQL; a evidência
anterior de issue 0032 foi **218 passed,
69 skipped** e **69 passed, 218 deselected**. A evidência anterior de issue 0031 foi **216 passed,
69 skipped** e **69 passed, 216 deselected**. A evidência anterior de issue 0030 foi **215 passed,
69 skipped** e **69 passed, 215 deselected**. A evidência anterior de issue 0026 foi **203 passed,
68 skipped** e **68 passed, 203 deselected**. A evidência anterior de issue 0022 foi **199 passed,
66 skipped** e **66 passed, 199 deselected**. A evidência anterior de issue 0021 foi **198 passed,
65 skipped** e **65 passed, 198 deselected**. A evidência anterior de issue 0020 foi **197 passed,
64 skipped** e **64 passed, 197 deselected**; a evidência de issue 0024 foi **193 passed,
61 skipped** e **61 passed, 193 deselected**; a evidência de issue 0018 foi **192 passed,
61 skipped** e **61 passed, 192 deselected**. Os resultados **183/60** e
**60/183** (issue 0017),
**177/56** e **56/177** (issue 0016),
**175/48** e **48/175** (issue 0015),
**169/42** e **42/169** (issue 0014),
**160/40** e **40/160** (issue 0013),
**151/36** e **36/151** são evidência
histórica de issue 0011; **146/36** e **36/146** são evidência
histórica de issue 0010; **143/36** e **36/143** são evidência histórica de
issue 0012; **122/33** e **33/122** são evidência histórica de
issue 0007. Esses
resultados locais não comprovam Redis, DigiSac, Groq, réplicas, deployment ou
produção.

## Próximas especificações e bloqueios

SPEC-0007 é a especificação canônica do **Milestone A — External Directory
Foundation** e foi implementada localmente pelo issue 0012. Ela registra a evidência
autorizada de base, Bearer via configuração segura, endpoints/payloads de
Departments e Companies, paginação `Pagina=N` e limite de 100 requisições por
minuto; tokens de exploração comprometidos não podem ser registrados. A
implementação deve aplicar as salvaguardas de reconciliação completa da SPEC,
sem inferir nomes de campos/parâmetros além dos observados.

SPEC-0008–SPEC-0012 são os contratos canônicos dependentes para **DigiSac
Contact Identity Foundation**, **DigiSac–Acessórias Identity Resolution**,
**Department Mapping**, **Durable Request Creation** e **Administração de
vínculos de identidade**. SPEC-0008 e seu slice de
persistência/upsert de ticket/hydration individual foram implementados pelo
issue 0013; o issue 0051 corrigiu a preservação do backoff das referências
repetidas. O issue 0014 implementa o full backfill: `perPage` alto pode
concluir uma página no tenant atual, e `page=N` com `currentPage`/`lastPage`
mantém o fallback paginado, deduplicado por `contact.id` e seguro contra não
avanço. SPEC-0010 v1.2 teve sua governança inicial aprovada e foi implementada
pelos issues 0016, 0020 e 0026: o mapping global por IDs externos estáveis é administrado por
procedimento `manual_db`, sem UI/endpoint e com ator opcional; regras e
avaliações de ciclo são persistidas somente no PostgreSQL; a atribuição usada em
cada avaliação fica limitada aos limites persistidos do ciclo e só é chamada
após a identidade canônica do ticket estar confirmada. SPEC-0011 v1.4 tem
contrato Request e políticas de duplicidade, retry e reconciliação implementados
pelos issues 0017–0019, 0021–0022 e 0026; as migrations `0019`/`0020` e a operação durável preservam a
classificação e não inventam idempotency key do provider. Erros de conexão,
timeout ou protocolo sem marcador explícito de pré-envio ficam em
`reconciliation_required` e não podem iniciar outro POST; adapters do mesmo
provider no processo compartilham a janela configurada sem persistir segredo ou
payload. `429` sem prova documentada de não criação segue a mesma fronteira:
status e `Retry-After` não autorizam segundo POST. SPEC-0009 v1.1 foi a versão
inicial implementada pelos issues 0015 e 0026: a variante
móvel brasileira permanece evidência conservadora, a confirmação inicial é o
procedimento controlado `manual_db`, e os resultados locais são persistidos
somente no PostgreSQL.
SPEC-0012 registra a evolução aprovada desse procedimento para uma API interna
autenticada por `ADMIN_API_TOKEN`, com lista/detalhe, busca de empresas,
confirmação, rejeição e redescoberta de um contato. Os issues 0038 e 0039
implementam localmente as três leituras e os comandos de confirmação/rejeição,
com PostgreSQL como autoridade, paginação determinística, idempotência durável
e projeções sem valores de telefone/email/evidência. O issue 0040 completa o
slice de redescoberta com o mesmo ledger e sem provider, Redis ou recuperação de
ciclos. O cenário
inicial possui um único operador e não exige cadastro de usuários, IdP, JWT ou
RBAC. O token deve ficar em secret manager/ambiente protegido, e um frontend
futuro é cliente fino dessa API, não autoridade de domínio. A SPEC não altera
matching conservador, não cria Request e não modifica resoluções históricas de
ciclo.
O issue 0034 moveu a coordenação transitória genérica para
`src/core/provider_coordination.py`, preservando o contrato e os escopos dos
adapters. O issue 0036 separou o transporte HTTP em
`src/core/acessorias_request_provider.py` e manteve a operação durável e os
imports compatíveis em `src/core/acessorias_requests.py`, sem alterar o
contrato do Request.
O delta documental v1.4 da SPEC-0005 foi implementado pelo issue 0041. Ele
alcançou somente a reconciliação de PRD, arquitetura e rastreabilidade com o
baseline implementado `0022`/`238+76` e a superfície administrativa da
SPEC-0012; o issue 0041 não alterou código, migration, UI ou alegação de
produção. O issue 0042 implementa o shell/sessão/BFF, o issue 0043 implementa o
incremento de leitura e o issue 0044 implementa as ações de confirmação,
rejeição e discovery da SPEC-0013. A entrega local não representa aceitação de
produção nem provisionamento dos segredos administrativos.

Milestone F continua fora do conjunto: exige decisão de produto após a criação
de Request ser comprovada. Não alterar as SPEC-0001–0006 concluídas para
atribuir retroativamente esses comportamentos; elas continuam descrevendo
somente os contratos já estabelecidos.

## Arquivos não ativos

Não há arquivos não ativos neste conjunto.

Não há especificações superseded, deprecated ou template. Um contrato que vier a
ser substituído deve ser preservado com status apropriado, apontar ao sucessor
canônico e sair da tabela de ativos sem apagar seu histórico.

## Fluxo

1. Planejamento referencia a especificação aplicável e registra dependências/decisões abertas.
2. A passagem de issues decompõe somente especificações prontas, sem redefinir seus contratos.
3. Build implementa issues aprovadas, executa a verificação exigida e atualiza o status da especificação com evidência. O isolamento da suíte (issue 0001), o runner descartável (0002), a verificação operacional (0004), a remoção das superfícies de diagnóstico (0006), a reconciliação documental v1.3 (0007 e 0009), a publicação de SPEC-0006 (0008), o Milestone A de SPEC-0007 (0012), o Milestone B de SPEC-0008 (0013, 0014 e 0026), o Milestone C de SPEC-0009 (0015 e 0026), o Milestone D de SPEC-0010 (0016, 0020 e 0026) e a preparação/recuperação do Milestone E (0026) estão implementados. O delta v1.4 da SPEC-0005 foi concluído pelo issue 0041; o shell/sessão/BFF, a leitura e as ações da SPEC-0013 foram implementados pelos issues 0042–0044; o contexto e o coordenador bounded da retirada de filas legadas foram implementados pelo issue 0052; a idempotência genérica de webhook e seu handoff PostgreSQL foram implementados pelo issue 0053.
