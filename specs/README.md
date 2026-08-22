# Implementation specifications

Baseline de especificações revisada no passe de specs de 2026-08-14. PRD e arquitetura estão presentes como baselines derivados da implementação; código, migrations, configuração e testes continuam prevalecendo para o comportamento atual. As especificações são contratos vinculantes para trabalho futuro e não aprovam políticas de produto ainda abertas.

## Especificações ativas

| ID | Especificação | Status | Prioridade/Fase | Dependências | Resumo |
| --- | --- | --- | --- | --- | --- |
| SPEC-0001 | [Contrato compartilhado de dados e análise](0001-shared-data-and-analysis-contract.md) | Baseline ativo v1.5; boundaries estruturais 0032, 0033 e 0035; auditoria Redis 0037 | P0 / baseline | — | Fonte durável, integridade, contrato IA, migrações, fronteiras de privacidade e limpeza manual allowlisted de resíduos Redis. |
| SPEC-0002 | [Webhook DigiSac e API de consulta](0002-digisac-webhook-and-query-api.md) | Baseline ativo v1.5; boundaries estruturais 0030 e 0033 | P0 / baseline | SPEC-0001 | HMAC, normalização, eventos e consultas atualmente sem versão; não há superfície de diagnóstico de webhook. |
| SPEC-0003 | [Finalização durável, contexto e mídia](0003-durable-finalization-and-media.md) | Baseline ativo v1.6; boundaries estruturais 0029, 0031 e 0035; auditoria Redis 0037 | P0/P1 | SPEC-0001, SPEC-0002 | Ciclo persistente único, contexto, mídia, retry e recuperação concorrente; áudio transitório além do limite IA e dead-letter seguro; limites persistidos consumidos por roteamento; resíduos legados tratados sem tocar estado ativo. |
| SPEC-0004 | [Baseline reprodutível de testes e verificação](0004-reproducible-verification-baseline.md) | Implementado v1.7 | P0/P1 | SPEC-0001–0003 | Suíte rastreada, isolamento, runner descartável e evidência local separada por etapa. |
| SPEC-0005 | [Reconciliação do baseline documental](0005-documentation-baseline-reconciliation.md) | Implementado v1.4; issue 0041 | P1 / reconciliação documental | SPEC-0002–0004, SPEC-0006, SPEC-0012 | Reconciliou PRD/arquitetura/rastreabilidade com `0022`/`238+76` e a API administrativa, sem alegar UI. |
| SPEC-0006 | [Documentação da API HTTP e contrato OpenAPI](0006-api-documentation-and-openapi-contract.md) | Implementado v1.2 | P1 / documentação de compatibilidade | SPEC-0001–0005, SPEC-0012 | Publica OpenAPI/Swagger/ReDoc para as oito rotas originais e as seis rotas administrativas internas montadas. |
| SPEC-0007 | [Fundação do diretório externo Acessórias](0007-acessorias-external-directory-foundation.md) | Implementado localmente v1.1; issue 0012; boundary estrutural 0034 | P0 / Milestone A | SPEC-0001, SPEC-0004, configuração segura de credencial | Diretório PostgreSQL de empresas, contatos, departamentos e relações, com reconciliação completa paginada, retry e segurança; não cria Request nem identidade DigiSac. |
| SPEC-0008 | [Fundação de identidade de contato DigiSac](0008-digisac-contact-identity-foundation.md) | Implementado localmente v1.4; issues 0013, 0014 e 0026; boundary estrutural 0028 | P0 / Milestone B | SPEC-0001, SPEC-0002, SPEC-0004, SPEC-0007 | Contato mínimo por `contact.id`, upsert de ticket, provenance canônica do contato no ciclo, hydration individual e full backfill idempotentes; sem resolução de empresa. |
| SPEC-0009 | [Resolução de identidade DigiSac–Acessórias](0009-digisac-acessorias-identity-resolution.md) | Implementado localmente v1.2; issues 0015 e 0026 | P1 / Milestone C | SPEC-0001, SPEC-0004, SPEC-0007, SPEC-0008 | Evidência e candidatos conservadores, vínculos muitos-para-muitos, resolução por ciclo e gate de preparação; confirmação é manual, nunca automática. |
| SPEC-0010 | [Mapeamento de departamento DigiSac para Acessórias](0010-digisac-acessorias-department-mapping.md) | Implementado localmente v1.3; issues 0016, 0020 e 0026; boundary estrutural 0030 | P1 / Milestone D | SPEC-0001, SPEC-0003, SPEC-0007–0009 | Regras globais por IDs externos estáveis, seleção da atribuição dentro dos limites do ciclo, auditoria de lifecycle, snapshots contra `company_departments` e gate após identidade confirmada; não usa IA nem cria Request. |
| SPEC-0011 | [Criação durável de Request Acessórias](0011-durable-acessorias-request-creation.md) | Implementado localmente v1.4; issues 0017–0019, 0021–0022 e 0026; boundaries estruturais 0034 e 0036 | P1 / Milestone E | SPEC-0001, SPEC-0003, SPEC-0007–0010 | Criação multipart externa (`tipo=E`) durável, preparação explícita, recuperação somente pré-POST comprovada, limite Sliding Window compartilhado no processo, payload pré-POST validado antes do marcador, sem idempotency key do provider e reconciliação manual de `429` incerto. |
| SPEC-0012 | [Administração de vínculos contato DigiSac–empresa Acessórias](0012-administrative-contact-company-link-management.md) | Implementado localmente v1.1; issues 0038, 0039 e 0040 | P1 / Milestone C.1 | SPEC-0001, SPEC-0006–0009; `ADMIN_API_TOKEN` em secret manager/ambiente protegido | API interna autenticada para listar, consultar, confirmar, rejeitar e redescobrir vínculos auditáveis; não cria Request nem reavalia ciclos históricos. |
| SPEC-0013 | [Interface web administrativa para conciliação de identidade](0013-administrative-identity-link-review-ui.md) | Implementado localmente v1.5; issues 0042–0044 | P1 / Milestone C.2 | SPEC-0012; `ADMIN_API_TOKEN`, `ADMIN_UI_PASSWORD` e `ADMIN_SESSION_SECRET` em ambiente protegido | Fundação FastAPI com login/logout, sessão assinada, fila, detalhe, busca e ações de confirmação/rejeição/discovery via BFF local; sem matching no frontend ou acesso direto ao banco. |

A evidência mais recente registrada para o runner de SPEC-0004 é **253 passed,
76 skipped** na etapa offline e **76 passed, 253 deselected** na etapa
PostgreSQL descartável (issue 0044, sem nova migration; head
`0022_identity_discovery_command`). A evidência anterior de issue 0040 foi **238 passed,
76 skipped** na etapa offline e **76 passed, 238 deselected** no PostgreSQL.
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
issue 0013. O issue 0014 implementa o full backfill: `perPage` alto pode
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
3. Build implementa issues aprovadas, executa a verificação exigida e atualiza o status da especificação com evidência. O isolamento da suíte (issue 0001), o runner descartável (0002), a verificação operacional (0004), a remoção das superfícies de diagnóstico (0006), a reconciliação documental v1.3 (0007 e 0009), a publicação de SPEC-0006 (0008), o Milestone A de SPEC-0007 (0012), o Milestone B de SPEC-0008 (0013, 0014 e 0026), o Milestone C de SPEC-0009 (0015 e 0026), o Milestone D de SPEC-0010 (0016, 0020 e 0026) e a preparação/recuperação do Milestone E (0026) estão implementados. O delta v1.4 da SPEC-0005 foi concluído pelo issue 0041; o shell/sessão/BFF, a leitura e as ações da SPEC-0013 foram implementados pelos issues 0042–0044.
