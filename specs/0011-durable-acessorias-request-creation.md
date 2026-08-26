# SPEC-0011 — Criação durável de Request Acessórias

- **Status:** implementada localmente pelos issues 0017–0019, 0021–0022, 0026 e 0047; boundaries estruturais pelos issues 0034 e 0036; evidência descartável, sem provider/produção
- **Versão:** 1.5 (issue 0047 adiciona gate de confidence e fixa criação automática como Request interno)
- **Prioridade/Fase:** P1 / Milestone E — Durable Acessórias Request Creation
- **Rastreabilidade:** PRD §§4, 5.5, 8 e 10; ARCHITECTURE §2.1; `IMPLEMENTATION_PLAN.md` Milestone E; SPEC-0001, SPEC-0003 e SPEC-0007–0010; diretiva do Product Owner e documentação oficial atual da API Acessórias de 2026-08-14
- **Dependências:** SPEC-0001, SPEC-0003, SPEC-0007 e SPEC-0008 implementadas; Milestones C (SPEC-0009) e D (SPEC-0010) concluídos para o ciclo elegível; credencial operacional segura disponível

**Evidência de implementação (2026-08-14):** issue 0017 adiciona a migration
Alembic `0019_acessorias_request_creation`, o adapter multipart de escrita, a
operação durável por ciclo, claims/leases conservadores e reconciliação
`manual_db`. O runner descartável passou compileall, Pyright estrito,
**183 passed, 60 skipped** offline e **60 passed, 183 deselected** em
PostgreSQL 16; isso é evidência local sintética/descartável e não comprova
credencial, provider, Redis, deployment ou produção.

**Correção de transporte (2026-08-17):** issue 0018 mantém `reconciliation_required`
para `requests.ConnectionError`, timeout ou falha de protocolo sem prova forte de
pré-envio. Somente a fronteira que puder provar que o POST não começou pode usar o
marcador explícito `AcessoriasRequestPreSendError` e seguir o retry limitado; não
se interpreta a exceção padrão do `requests` como prova de ausência remota.

**Evidência adicional (2026-08-17):** o teste focado passou com **10 passed, 5
skipped**; o runner descartável passou compileall, Pyright, **192 passed, 61
skipped** offline, Alembic `0019_acessorias_request_creation` e **61 passed,
192 deselected** em PostgreSQL 16. Isso é evidência local sintética/descartável,
sem credencial real, provider, Redis, deployment ou produção.

**Correção de coordenação (2026-08-17):** issue 0019 compartilha o Sliding
Window entre instâncias do adapter de Request no mesmo processo, separado por
endpoint/configuração do provider. A admissão é serializada antes do POST; a
expiração da janela e o isolamento entre configurações permanecem
determinísticos. O estado é apenas memória transitória e não contém token,
header ou payload.

**Evidência da correção (2026-08-17):** o teste focado passou com **12 passed,
5 skipped**; o runner descartável passou compileall, Pyright, **197 passed, 61
skipped** offline, Alembic `0019_acessorias_request_creation` e **61 passed,
197 deselected** em PostgreSQL 16. Os testes adicionados cobrem instâncias
distintas, expiração, isolamento por endpoint e admissão concorrente. Isso é
evidência local sintética/descartável, sem credencial real, provider, Redis,
deployment ou produção.

**Correção da fronteira pré-POST (2026-08-17):** issue 0021 carrega e valida o
payload persistido antes de registrar `post_started_at`. Falha de leitura ou
validação permanece em `retryable_failure` com a categoria sanitizada
`payload_load_failed`, sem marcador de POST, sem chamada ao provider e com
replay posterior permitido; a recuperação de lease que já possui marcador
genuíno continua em `reconciliation_required`.

**Correção de `429` (2026-08-17):** issue 0022 classifica uma resposta `429`
sem `id` e sem sinal documentado de não criação como
`reconciliation_required`, preservando o status e a categoria sanitizada. O
status e `Retry-After` não provam ausência remota; portanto não há segundo POST,
retry automático ou liberação implícita. O reprocessamento só pode ocorrer após
`manual_db` registrar `SolID` ou liberar explicitamente com prova de ausência.
O contrato atual do provider não documenta um caminho de `429` seguro para
retry.

**Evidência da correção de `429` (2026-08-17):** o teste focado passou com **14
passed, 7 skipped**; o runner descartável passou compileall, Pyright, offline
pytest (**199 passed, 66 skipped**), Alembic `0019_acessorias_request_creation`
e PostgreSQL pytest (**66 passed, 199 deselected**). A cobertura prova um único
POST para `429` incerto, persistência de reconciliação, replays concorrentes
sem novo POST e reconciliação manual de `SolID`. A evidência é local
sintética/descartável, sem credencial real, provider, Redis, deployment ou
produção.

**Manutenção estrutural (2026-08-20):** issue 0034 moveu o estado e a
admissão genérica do Sliding Window para `src/core/provider_coordination.py`.
Os adapters de Request continuam compartilhando a janela no processo por
endpoint/configuração, sem mudança no payload, retry, reconciliação, operação
durável ou no limite contratado.

**Manutenção estrutural adicional (2026-08-20):** issue 0036 separou o
contrato de payload/outcome e o adapter HTTP em
`src/core/acessorias_request_provider.py`. `src/core/acessorias_requests.py`
continua responsável pela operação PostgreSQL, elegibilidade, claims/leases,
marcador pré-POST, persistência de outcome e reconciliação, mantendo os
imports de provider existentes como compatibilidade. Não houve alteração de
payload, autenticação, retry, rate admission, idempotência, concorrência,
reconciliação, schema ou comportamento externo.

**Evidência da boundary (2026-08-20):** o teste focado de provider/Request,
preparação e Directory passou com **41 testes aprovados e 12 skips**. O runner
descartável passou compileall, Pyright estrito, **224 testes offline e 69
skips**, Alembic `0020_cycle_contact_provenance` e **69 testes PostgreSQL, 224
desselecionados**. A evidência é local/sintética/descartável e não comprova
provider, Redis, deployment ou produção.

**Integração de preparação (2026-08-17):** issue 0026 torna explícita a ordem
terminal → identidade confirmada → snapshot de mapping resolvido → operação
durável → provider POST. A recuperação interna de `mapping_missing` só pode
reabrir uma operação com prova durável de que nenhum POST começou; operações
com marcador de envio, `SolID`, tentativa, estado incerto ou reconciliação
continuam fora de retry automático. A migration `0020` preserva a proveniência
do contato do ticket e o estado de auditoria da preparação. A execução local
descartável passou compileall, Pyright estrito, **203 passed, 68 skipped**
offline e **68 passed, 203 deselected** em PostgreSQL 16, sem provider ou
produção.

**Gate de confidence e tipo interno (2026-08-26):** issue 0047 preserva o
contrato de IA e do banco em `0..1`, mas aplica na fronteira durável de Request
a regra de negócio `confidence * 10 >= 5.0`, equivalente a `confidence >= 0.50`.
O valor de fronteira é aceito; `NULL`, valores inválidos/não finitos, fora de
`0..1` e abaixo do limite bloqueiam antes do provider e novamente antes de
`post_started_at`. O bloqueio é persistido como resultado sanitizado, sem
chamada, tentativa, `SolID` ou marcador de POST. Todo POST automático futuro
usa `tipo=I` (interno); operações antigas com evidência de POST não são
reescritas nem convertidas. A execução canônica passou compileall, Pyright,
**269 passed, 82 skipped** offline, Alembic `0023_manual_reconciliation` e
**82 passed, 269 deselected** em PostgreSQL 16 descartável; isso não comprova
provider, credenciais, deployment ou produção.

## Objetivo e não objetivos

Definir o efeito durável que cria um Request Acessórias interno depois que uma classificação terminal elegível, com confidence suficiente, uma empresa Acessórias inequivocamente resolvida e um departamento Acessórias válido já existirem. O contrato deve manter a ligação CAI–Request, incluindo o `SolID` retornado, e garantir que falha, reconciliação ou intervenção operacional no provider nunca desfaça, corrompa ou reescreva a classificação persistida.

Esta especificação não sincroniza diretórios, resolve identidade, mapeia departamento, altera IA, cria UI ou endpoint HTTP público, nem dispara a criação no webhook de fechamento. Também não cobre edição (`POST /requests/{id}`), comentários, `statusSol`, `descPrivate`, anexos, responsáveis, reabertura, finalização, mudança entre Request interno/externo ou qualquer sincronização de ciclo de vida. Esses comportamentos pertencem ao Milestone F e a uma SPEC posterior.

## Estado de referência e fronteira canônica

O checkout atual possui o adapter de criação, migrations `0019` e `0020`, estado de
entrega, `SolID`, chamada externa controlada e testes correspondentes. SPEC-0007
é canônica para o diretório Acessórias, SPEC-0009 para a resolução de empresa e
SPEC-0010 para o departamento selecionado e seu snapshot. Esta SPEC é canônica
somente para criar, registrar, recuperar e reconciliar o Request.

A operação deve usar o provider boundary Acessórias já estabelecido pelos milestones anteriores; webhook, worker de IA, handler HTTP e persistência não podem conter chamadas diretas ao provider. PostgreSQL é a autoridade da operação; Redis pode coordenar trabalho, mas não pode ser sua única cópia nem decidir seu resultado.

## Contrato Acessórias de criação

1. O adaptador deve enviar `POST https://api.acessorias.com/requests` com `Authorization: Bearer <token>` e `multipart/form-data`. O token vem de configuração segura, nunca é persistido ou registrado em logs, headers, métricas, erros ou estado operacional.
2. Cada POST deve conter somente os campos deste milestone: `assunto`, `empresa`, `departamento`, `prioridade`, `descricao` e `tipo=I`. A abertura automática é sempre interna, nunca externa; não há override, configuração ou fallback para `tipo=E`. Não deve enviar `arquivo`/`arquivo[]`, `data_prazo` ou qualquer campo de lifecycle.
3. Antes da tentativa HTTP, o adaptador deve validar localmente, sempre que possível: `assunto` string não vazia com no máximo 100 caracteres; `empresa` como ID externo ou CNPJ inequivocamente associado à empresa local; `departamento` como ID inteiro Acessórias vinculado à empresa no diretório atual; `prioridade` em `0`, `1`, `2`, `3`; e `descricao` string. A escolha entre ID externo e CNPJ para `empresa` pertence ao adapter, mas a identidade local persistida deve continuar inequívoca.
4. `assunto` deve usar o protocolo persistido no ciclo quando disponível, no formato `[protocolo] - title`, seguido do `title` persistido na classificação. Se exceder 100 caracteres, o corte deve ser determinístico e resultar em valor não vazio. Sem protocolo, usa-se somente o `title`. `descricao` deve derivar exclusivamente da `description` persistida. Não se chama a IA novamente e não se altera a classificação para adequá-la ao provider.
5. A prioridade inicial é política de domínio centralizada `2` (Média). Ela não pode ser inferida de `confidence` ou `intent_type`, espalhada como magic number, nem configurada por empresa/departamento neste milestone.
6. Sucesso só é confirmado por resposta que contenha `id` não vazio. Esse valor é o `SolID` externo a persistir; `msg` não é identidade e não pode ser usado para inferir sucesso. A resposta documentada é compatível com `{ "id": "1", "msg": "Solicitação 1 criada com sucesso!" }`.
7. O provider não documenta idempotency key para este endpoint. A implementação não deve inventar header ou parâmetro de idempotência; a prevenção de duplicidade é responsabilidade durável do CAI.
8. O limite documentado é 100 requests/minute com Sliding Window. Os adapters de Request no mesmo processo devem aplicar limite conservador compartilhado por endpoint/configuração do provider, respeitar `Retry-After` quando presente e usar backoff limitado quando ausente. Não precisa consumir toda a capacidade; processos distintos continuam sujeitos à topologia operacional e à verificação externa do provider.

9. O gate de negócio usa a confidence persistida da classificação: a escala de
   decisão é `0..10`, calculada como `confidence * 10`, e o mínimo aceito é
   `5.0` (`confidence >= 0.50`). Exatamente `0.50` é permitido. `NULL`, tipo
   não numérico, valor não finito ou valor fora de `0..1` é inválido e deve
   bloquear a abertura.

## Elegibilidade e operação durável

1. A criação é etapa posterior da integração, iniciada somente após o ciclo CAI atingir estado terminal permitido pelo contrato de classificação. `completed` é elegível. `completed_with_warnings` é elegível somente quando as warnings não representarem ausência de dado necessário à criação. `failed`, `media_blocked`, estados não terminais e classificação inexistente não criam Request. Esta integração não pode alterar a máquina de estados para obter elegibilidade.
2. A resolução de empresa do ciclo deve ser exatamente uma empresa válida do contrato de identity resolution, preferencialmente pela identidade externa Acessórias já persistida. `unresolved`, `ambiguous` e `conflict` bloqueiam a criação; candidato não é confirmação.
3. O resultado do Milestone D deve fornecer departamento Acessórias mapeado do departamento DigiSac, válido para a empresa na relação corrente `company_departments`. Ausência de mapping, departamento inválido ou qualquer escolha por nome, `intent_type`, fallback ou first-match bloqueia a criação.
4. A confidence deve ser avaliada antes de a operação alcançar o provider e
   reavaliada na fronteira final, depois da carga/validação do payload e
   atomicamente antes de `post_started_at`. Um ciclo abaixo do limite ou com
   confidence inválida fica em `definitive_failure`, com categoria sanitizada,
   sem provider, tentativa, `SolID`, marcador de POST ou retry automático; a
   classificação original não é alterada.
5. Antes de todo POST, deve existir uma operação PostgreSQL durável com ao menos: ciclo de origem, classificação, identidade de conversa/ticket DigiSac quando útil, empresa e departamento Acessórias resolvidos, representação segura/fingerprint do payload, estado, metadados de tentativa, `SolID` quando conhecido, timestamps e erro/estado de reconciliação sanitizados. Campos e retenção finais seguem as convenções e o contrato de privacidade do repositório; logs não podem expor conteúdo sensível, PII, token ou payload bruto.
6. Um ciclo pode originar no máximo um Request automático neste milestone. Uma constraint durável equivalente a `UNIQUE(source_cycle_id)` deve fazer replay convergir para a mesma operação e impedir que retries concorrentes criem duas operações ou dois POSTs.
7. Claim, lease ou locking durável deve assegurar que dois workers/processos não executem a mesma operação ao mesmo tempo. Recuperação de claim abandonado não pode causar outro POST sem classificar conservadoramente o estado anterior.

## Estados, falhas, retry e reconciliação

1. Os nomes finais podem seguir convenções existentes, mas a semântica deve distinguir: `not_started`/`pending` (persistida, sem POST), `attempting`, `completed` (sucesso com `SolID` duravelmente salvo), `definitive_failure`, `retryable_failure` e `reconciliation_required`. Pode existir estado operacional separado para credencial/permissão aguardando correção.
2. Erro JSON do provider com chave `Erro` deve participar da classificação; status HTTP isolado não é autoridade suficiente. Erro business/validação que rejeita a criação é `definitive_failure`. Bearer ausente/inválido ou falta de permissão é falha operacional sanitizada, sem retry agressivo e sem mudança da classificação. `429` é transitório, mas só é retryable quando o adapter puder determinar que não houve criação remota.
3. Uma nova tentativa automática é permitida somente com evidência forte de que o POST não foi processado: falha local antes de iniciar HTTP, falha de conexão explicitamente marcada pela fronteira como anterior ao envio, rejeição explícita sem criação, ou erro transitório/`429` documentado para o qual o adapter pode provar ausência de sucesso remoto. A exceção padrão de conexão do transporte não fornece essa prova. A nova tentativa deve ser limitada, respeitar o rate limit e usar backoff.
4. Timeout após envio, conexão encerrada após envio, resposta ilegível após possível processamento, `5xx` sem prova de não processamento, sucesso sem `id`, queda durante tentativa ou qualquer situação em que o provider possa ter aceitado o POST sem `SolID` duravelmente persistido é `reconciliation_required`. Não pode haver retry automático nem segundo Request; a operação deve permanecer visível operacionalmente.
5. Como não há idempotency key nem consulta documentada por chave fornecida pelo cliente, o primeiro milestone não pode confirmar automaticamente por correlação frágil de assunto/data. A reconciliação inicial é administrativa, diretamente no PostgreSQL e por consulta à API Acessórias, sem UI.
6. Em operação controlada, o operador pode consultar a Acessórias e: (a) se o Request existir, registrar explicitamente seu `SolID` e marcar `completed`/`reconciled`; ou (b) somente com prova de que não foi criado, liberar explicitamente uma nova tentativa. Deve registrar timestamp, origem `manual_db` e ator apenas quando existir identidade administrativa confiável. Não se inventa ator.
7. Crash antes do POST retorna a estado recuperável e safe-to-retry. Crash durante a tentativa exige a evidência duravelmente registrada pelo adapter para classificação conservadora. Se o provider retornou sucesso com `id` mas o processo caiu antes do commit local, o resultado é incerto e não se repete o POST. Após commit de `completed`, qualquer execução posterior é no-op.

## Compatibilidade, observabilidade e verificação

1. A criação não altera as oito rotas HTTP, webhook, finalização, contrato IA nem classificações existentes; não há endpoint público para dispará-la. A operação nasce do pipeline interno depois dos pré-requisitos.
2. Logs, métricas e estado operacional podem expor IDs seguros, estado, tentativas, duração, categoria sanitizada, fingerprint e referência externa segura. Não podem conter token, header, payload bruto, telefone, email, conversa, `title`/`description` completos ou outros dados sensíveis.
3. Doubles determinísticos e testes PostgreSQL descartáveis devem cobrir: ciclo `completed` elegível com confidence aceita cria operação; replay usa a mesma operação sem segundo POST; confidence abaixo de `0.50` ou inválida, empresa unresolved/ambiguous, mapping ausente e departamento não permitido não chamam provider; assunto acima de 100 tem corte determinístico; payload contém `prioridade=2` e `tipo=I`; e anexos/`data_prazo` não são enviados.
4. Devem cobrir sucesso com `id` (`completed` + `SolID`), sucesso sem `id` (não completed), `Erro` business definitivo, auth/permissão operacional, `429`, `5xx`, o marcador explícito de conexão pré-envio retryable e a conexão/protocolo ambíguos sem segundo POST, além de timeout/perda de conexão pós-envio como `reconciliation_required`.
5. Devem provar claim concorrente, crash antes/durante/depois do POST, replay de `completed` como no-op, reconciliação manual com `SolID`, liberação manual explícita após prova de ausência remota e ausência de token/PII sensível em logs e estado. A implementação deve passar a suíte offline aplicável, Pyright estrito e o runner canônico de SPEC-0004; doubles não comprovam credenciais, provider real ou produção.

## Decisões registradas e evidência de implementação

Endpoint, autenticação, formato multipart, campos, prioridade padrão,
limite, confirmação por `id`, ausência de idempotency key, retry conservador,
reconciliação manual e operação administrativa inicial estão aprovados e foram
implementados pelos issues 0017–0019 e 0021–0022. A credencial operacional continua
necessária para uma chamada real; doubles e o runner não comprovam provider ou
produção. A correção do issue 0018 é conservadora e não amplia a autorização de
retry: erros de transporte sem prova explícita de pré-envio permanecem sujeitos
à reconciliação manual. Issue 0019 apenas corrige a coordenação transitória de
admissão entre adapters do mesmo processo, sem alterar payload, retry ou
reconciliação. Issue 0022 aplica essa mesma fronteira conservadora a `429`:
sem prova documentada de não criação, o adapter não usa status ou
`Retry-After` como autorização para novo POST.
Isso não amplia o escopo ao lifecycle do Milestone F.

Issue 0047 corrige a política ativa de abertura automática: a operação só
alcança o provider quando a confidence persistida normalizada para `0..10` é
maior ou igual a `5.0`, e o payload usa sempre `tipo=I`. O contrato de IA e a
coluna persistida continuam em `0..1`; bloqueios são resultados duráveis
sanitizados e não Requests abertos. A implementação mantém evidência de
operações que já iniciaram POST imutável e usa `tipo=I` somente em tentativas
futuras independentemente seguras.
