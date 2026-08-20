# SPEC-0008 — Fundação de identidade de contato DigiSac

- **Status:** implementada localmente pelos issues 0013, 0014 e 0026
- **Versão:** 1.4 (issue 0026 adiciona a proveniência durável do contato de ticket)
- **Prioridade/Fase:** P0 / Milestone B — DigiSac Contact Identity Foundation
- **Rastreabilidade:** PRD §§4, 5.5, 8 e 10; ARCHITECTURE §2.1; `IMPLEMENTATION_PLAN.md` Milestone B; SPEC-0001, SPEC-0002, SPEC-0004 e SPEC-0007
- **Dependências:** SPEC-0001, SPEC-0002, SPEC-0004 e SPEC-0007

**Evidência de implementação (2026-08-14):** issue 0013 adiciona a migration
Alembic `0016_digisac_contact_identity`, o cliente tipado para hydration
individual, upsert incremental de snapshots de ticket, claims/retries duráveis
no PostgreSQL e integração webhook sem chamada Contacts em linha. O runner
descartável mais recente (issue 0014) passou compileall, Pyright estrito,
**169 passed, 42 skipped** offline e **42 passed, 169 deselected** em
PostgreSQL 16; isso é evidência
local sintética/descartável e não comprova provider, Redis ou produção. A
evidência autorizada de Contacts, também em 2026-08-14, removeu o bloqueio de
paginação do full backfill e definiu seu contrato de fallback multi-page abaixo.
O issue 0014 implementa a aquisição tipada, validação, deduplicação global,
publicação transacional e CLI interna do full backfill; a verificação continua
local/sintética e descartável, sem credencial ou sincronização de produção.

O issue 0015 acrescenta, de forma aditiva, `raw_email` e `normalized_email`
para permitir a resolução exata prevista na SPEC-0009; esses campos não mudam
a identidade canônica `contact.id`.

**Integração de preparação (2026-08-17):** issue 0026 adiciona a migration
Alembic `0020_cycle_contact_provenance` e persiste no ciclo somente o
`data.contact.id` do snapshot canônico de ticket. O worker usa esse valor para
localizar o contato durável antes de chamar a resolução; `message.contactId`,
participantes de grupo e metadata derivada não podem substituí-lo. A execução
local descartável passou compileall, Pyright estrito, **203 passed, 68 skipped**
offline e **68 passed, 203 deselected** em PostgreSQL 16; isso não comprova
DigiSac, provider ou produção.

## Objetivo e não objetivos

Definir a representação local mínima, durável e reconciliável de um contato DigiSac. A identidade externa canônica **deve** ser `contact.id`; `contactId` nos eventos referencia essa mesma identidade. PostgreSQL é a autoridade local; Redis **não pode** ser usado como diretório, estado de hydration ou fonte de identidade.

Esta especificação **não** associa contatos a empresas Acessórias, não cria candidato, vínculo `confirmed`/`ambiguous`/`rejected`, Request, rota HTTP, interface administrativa ou sync de Users. Telefone, nome, `idFromService`, `jidId` e `lidId` **não podem** ser chaves de identidade ou matching. A normalização brasileira avançada e a resolução cross-system pertencem à SPEC-0009.

## Evidência autorizada e fronteira canônica

O tenant observado usa a base versionada `/api/v1`; a configuração concreta **deve** continuar usando `DIGISAC_API_BASE_URL` e `DIGISAC_API_KEY` pelos mecanismos existentes, sem hostname de tenant hardcoded. A integração atual já envia `Authorization: Bearer` a partir dessa configuração. Token de exploração é comprometido e **não pode** constar em spec, código, fixture, teste, exemplo, log ou migration; headers de autenticação também não podem ser logados.

As superfícies autorizadas, relativas à base configurada, são:

- `GET /contacts?perPage=5000&page=1`, que no tenant atual retornou os 3.249
  contatos em uma página (`total=3249`, `limit=5000`, `currentPage=1`,
  `lastPage=1`, `count=3249`); e
- `GET /contacts?perPage=2&page=2`, que confirmou `page=N` como avanço e
  `perPage=N` como tamanho solicitado (`total=3249`, `limit=2`, `skip=2`,
  `currentPage=2`, `lastPage=1625`, `from=2`, `to=4`);
- `GET /contacts/{contactId}`, observado/documentado para hydration individual.

O único webhook HTTP é `POST /webhook/digisac`. Seu tipo está em `payload.event`; o contrato atual trata `ticket.created`, `ticket.updated`, `message.created` e `message.updated`, e ignora os demais. SPEC-0008 não cria rota nova nem muda essa política.

O snapshot `data.contact` de `ticket.created` e `ticket.updated` é fonte incremental autorizada. Nele foram observados `id`, `idFromService`, `name`, `internalName`, `alternativeName`, `isGroup`, `isBroadcast`, `accountId`, `serviceId`, `createdAt`, `updatedAt`, `deletedAt` e o objeto `data`; em `contact.data`, `number`, `jidId`, `lidId` e outros campos operacionais. Em `message.created` e `message.updated`, há `data.contactId`, mas o snapshot completo não é obrigatório. A listagem observada devolve os equivalentes semânticos de nome, nome alternativo, nome interno, número, grupo, não lidas, última mensagem, ticket ativo e ID; campos operacionais não são requisito de persistência.

O slice implementado possui cliente Contacts para hydration individual, tabela
de contato, upsert incremental e full backfill. O diretório em
`src/core/digisac_directory.py` cobre
somente departamentos e Users. O cliente DigiSac existente continua sendo a
autoridade de configuração e política de autenticação/retry a ser reutilizada
ou estendida coerentemente.

## Dados, integridade e privacidade

1. Cada contato **deve** ter uma única linha por `contact.id` não vazio, tratado como identificador externo opaco e imutável. ID interno CAI não pode substituí-lo nem reinterpretá-lo.
2. A representação local **deve** reter somente metadata justificada por identidade, hydration, auditoria e reconciliação: ID externo, `name`, `alternativeName`, `internalName` quando presente, número bruto, número técnico normalizado, email bruto e sua forma normalizada, `is_group`, `accountId`, `serviceId`, timestamps `createdAt`/`updatedAt`/`deletedAt` quando presentes e metadata local de `synced_at`/`last_seen` e origem. Os nomes finais seguem as convenções do repositório.
3. Número normalizado **deve** converter dígitos Unicode decimais para ASCII e reter apenas dígitos. Número bruto ausente ou vazio não produz valor normalizado. Ele é evidência, não identidade: SPEC-0008 não conclui que dois números são a mesma pessoa ou empresa.
4. Grupo informado por `isGroup=true` **deve** ser persistido com `is_group`. Seu número técnico, inclusive formas com hífen, não pode ser tratado como telefone de pessoa/empresa; seu nome não pode confirmar vínculo automático.
5. `deletedAt` não nulo pode ser preservado como estado/metadata do provider. Ausência em listagem ou reconciliação **não pode** significar deleção, nem produzir remoção física ou apagar vínculos históricos futuros.
6. Constraints devem impedir duplicação de `contact.id` e transições impossíveis. A evolução é migration Alembic aditiva; startup não cria nem altera tabelas. Downgrade destrutivo deve recusar antes de perder dados, conforme SPEC-0001.
7. Logs, métricas e estado operacional podem conter ID de contato, execução, contagens, duração, operação/endpoint lógico e categoria sanitizada de erro. Não podem conter payload bruto, telefone completo, nome, texto de mensagem, token ou header de autorização.

## Ingestão, hydration, precedência e backfill

1. Um snapshot válido de `data.contact` em evento de ticket **deve** efetuar upsert idempotente por `contact.id`. Replay, concorrência e eventos fora de ordem não podem duplicar contato nem apagar atributo mais novo conhecido.
2. Mensagem com apenas `contactId` pode registrar necessidade de hydration deduplicada quando a metadata local for ausente ou insuficiente segundo regra documentada. Ela não pode consultar Contacts em linha nem iniciar uma chamada por mensagem repetida; a hydration pode ocorrer fora do caminho crítico pelo endpoint individual autorizado.
3. A precedência é: (a) `contact.id`/`contactId` define identidade; (b) snapshot de webhook mais recente atualiza metadata quando `updatedAt` for comparável; (c) hydration individual atualiza quando trouxer estado mais recente/confiável; (d) backfill/reconciliação não sobrescreve snapshot comprovadamente mais novo; (e) sem timestamps ordenáveis, o upsert deve ser conservador e idempotente, sem converter ausência em exclusão. Nenhuma fonte altera vínculos DigiSac–Acessórias.
4. O adaptador Contacts **deve** centralizar autenticação, timeout, retry, parse e conversão ao registro local. Webhook, worker de IA e handler HTTP não podem conter chamadas Contacts diretas. Ele deve reutilizar a política DigiSac existente: timeout/tentativas limitados, `429` transitório e `Retry-After` quando presente; na ausência dele, backoff limitado. Não há limite oficial de requests/minute autorizado nesta SPEC.
5. Full backfill **deve** solicitar `perPage` alto e seguro, definido por
   configuração ou constante técnica documentada. O valor observado `5000`
   permite uma única página no tenant atual, mas não garante que todo tenant,
   volume futuro ou limite do provider aceite qualquer tamanho arbitrário.
6. Cada resposta de listagem **deve** validar o envelope e usar `total`,
   `limit`, `currentPage` e `lastPage` como metadata de autoridade da execução.
   Quando `lastPage == 1`, o fetch termina na única chamada; quando
   `lastPage > 1`, deve continuar com o parâmetro validado `page=N`. `skip`,
   `from` e `to` são metadata adicional, não o mecanismo de avanço.
7. A execução **deve** deduplicar globalmente por `contact.id`, pois foi
   observada repetição entre páginas adjacentes. Repetir o mesmo contato deve
   ser idempotente, inclusive entre páginas e reexecuções.
8. Página repetida, `currentPage` que não avance, envelope/página inválido ou
   erro do provider **deve** falhar a execução sem declará-la completa. Não se
   pode interpretar esses casos como fim normal do backfill. Uma execução
   parcial nunca pode transformar ausência em exclusão ou inativação.

## Falhas, compatibilidade e verificação

1. Falhas de autenticação/autorização, payload inválido ou integridade devem encerrar a operação de modo sanitizado e preservar o último dado válido. Timeout, conexão e status transitórios seguem o retry limitado acima.
2. Credencial ausente ou Contacts indisponível não pode impedir o webhook de manter seu contrato atual; deve deixar estado recuperável e observável para hydration/backfill posterior.
3. A adição do diretório não pode alterar as oito rotas HTTP atuais, contrato de IA, finalização ou consultas sem versão. Exposição de contato exige SPEC própria e revisão de privacidade.
4. Doubles determinísticos devem provar snapshot de ticket, referência `contactId` sem busca por mensagem, hydration deduplicada, grupo, replay, precedência e preservação diante de dados sem ordenação. Para o full backfill, devem cobrir página única, fallback multi-page por `page=N`, envelope inválido/erro do provider, não avanço de `currentPage`, deduplicação entre páginas e falha parcial.
5. Testes PostgreSQL descartáveis devem provar migration para head, unicidade, upsert idempotente, rollback parcial, preservação da última observação válida e isolamento de Redis. Testes de adaptador devem cobrir credencial ausente, timeout, conexão, `429`/`Retry-After`, limite de tentativas, resposta inválida e ausência de PII/segredo em logs/estado.

## Critérios de aceitação e próximo slice

- O mesmo `contact.id` obtido por ticket ou hydration produz uma única identidade durável, sem chamada Contacts por mensagem repetida.
- Número bruto/normalizado e `is_group` são preservados sem matching, confirmação, Request ou resolução Acessórias.
- `deletedAt` é metadata conservada; ausência em listagem nunca apaga contato.
- O slice de migration/modelo/upsert de ticket/hydration individual foi implementado pelo issue 0013.
- O full Contacts backfill foi implementado pelo issue 0014 com o contrato de paginação, deduplicação e falha acima.

## Decisão residual mínima

Não permanece blocker material para a decomposição do full Contacts backfill:
o provider validou `perPage`, avanço por `page=N` e término por
`currentPage`/`lastPage`. A aceitação de `perPage=5000` é evidência do tenant
atual, não uma garantia universal; a implementação deve preservar o fallback
multi-page definido nesta SPEC. Não há decisão de produto pendente para este
slice.
