# SPEC-0012 — Administração de vínculos contato DigiSac–empresa Acessórias

- **Status:** leitura, confirmação/rejeição e redescoberta implementadas localmente nos issues 0038, 0039 e 0040
- **Versão:** 1.1
- **Prioridade/Fase:** P1 / Milestone C.1 — operação administrativa de identidade
- **Rastreabilidade:** PRD §§4, 5.2, 5.5, 8 e 10; ARCHITECTURE §§2.1, 3 e 13; `IMPLEMENTATION_PLAN.md`; SPEC-0001, SPEC-0006–0011
- **Dependências:** SPEC-0001, SPEC-0006, SPEC-0007, SPEC-0008 e SPEC-0009; diretório Acessórias sincronizado; `ADMIN_API_TOKEN` configurado com segurança

## Evidência do slice implementado

O issue 0038 implementa as três operações de leitura desta SPEC sob o prefixo
`/admin/acessorias`: triagem paginada de vínculos, detalhe de um contato
canônico e busca display-only de empresas presentes/ativas. O router exige
`ADMIN_API_TOKEN` com Bearer em todas as operações e falha no startup quando a
configuração não existe ou é vazia. A projeção usa somente PostgreSQL, cursores
opacos assinados e joins agregados; não executa discovery, hydration, sync,
Redis, providers, Request ou qualquer escrita de identidade/ciclo. Respostas
contêm apenas IDs externos, estados, nomes de exibição, disponibilidade,
categorias/contagens/horários de evidência e transições sanitizadas; valores de
telefone, email e evidência não fazem parte da projeção.

**Evidência da fatia de confirmação/rejeição (2026-08-21):** o issue 0039
adiciona os comandos autenticados `POST /admin/acessorias/contacts/{id}/identity-links/confirm`
e `POST /admin/acessorias/contacts/{id}/identity-links/{company_id}/reject`.
O domínio mantém a confirmação/rejeição sob lock do contato, registra `admin`/
`admin_api`, conserva transições e evidências e usa a migration Alembic
`0021_identity_admin_commands` para persistir hashes de chaves, fingerprint de
comando e resultado sanitizado. Replays com a mesma chave retornam o resultado
armazenado sem nova transição; reutilização incompatível retorna `409`, e falhas
transacionais não deixam reserva parcial. A validação descartável passou
compileall, Pyright, **237 passed, 74 skipped** offline, Alembic `0021` e
**74 passed, 237 deselected** em PostgreSQL 16. Isso é evidência local
descartável e não comprova secret manager, provider, Redis, deployment ou
produção. O issue 0040 implementa a redescoberta opcional, sem afirmar rollout
de produção.

**Evidência da fatia de redescoberta (2026-08-21):** o issue 0040 adiciona
`POST /admin/acessorias/contacts/{id}/identity-discovery`, com chave opaca,
resultado sanitizado por IDs externos e replay pelo mesmo ledger PostgreSQL.
A migration aditiva `0022_identity_discovery_command` permite o escopo
`identity_discovery` sem empresa-alvo; a transação mantém o lock do contato,
evidências e candidatos da descoberta determinística, sem provider, Redis,
hydration, sync, backfill, Request ou alteração de resolução histórica. A
validação descartável passou compileall, Pyright, **238 passed, 76 skipped**
offline, Alembic `0022` e **76 passed, 238 deselected** em PostgreSQL 16. Isso é
evidência local descartável e não comprova secret manager, provider, Redis,
deployment ou produção.

## Objetivo e não objetivos

Definir a superfície administrativa interna que permite revisar evidências e
vínculos entre um contato canônico DigiSac e uma empresa Acessórias, confirmar
explicitamente uma empresa ou rejeitar um vínculo candidato. Ela substitui a
alteração rotineira direta no PostgreSQL por uma operação HTTP autenticada,
auditável e idempotente, apta a sustentar um frontend futuro.

Esta SPEC não altera o algoritmo de matching da SPEC-0009, não cria confirmação
automática, não sincroniza diretórios, não faz hydration de contato, não cria ou
reprocessa Request, não reavalia ciclos históricos, não expõe uma API pública e
não cria cadastro de usuários, não integra IdP e não escolhe tecnologia de UI.
Também não autoriza editar nomes,
telefone, email, empresa ou dados canônicos do diretório por essa superfície.

## Estado de referência e fronteira canônica

SPEC-0008 é canônica para o contato DigiSac; SPEC-0007, para empresas e contatos
do diretório Acessórias; e SPEC-0009, para evidência, vínculo, estados e
resolução de identidade. Esta SPEC é canônica apenas para a camada de operação
administrativa sobre esses fatos já persistidos.

O checkout possui a fronteira de domínio `confirm_identity_link()` e a entidade
`identity_company_links`, com transições auditáveis e garantia de que duas
empresas não permaneçam confirmadas concorrentemente para o mesmo contato. A
implementação desta SPEC deve reutilizar essa fronteira — ou uma evolução
equivalente com as mesmas garantias — e não escrever tabelas de identidade
diretamente a partir do handler HTTP ou do frontend.

A resolução de identidade de ciclo é imutável. Confirmar ou rejeitar um vínculo
por esta API afeta apenas decisões futuras; não altera
`conversation_cycle_identity_resolutions`, não desbloqueia Request histórico e
não produz POST ao provider. Uma recuperação pré-POST comprovada continua sendo
operação distinta sob SPEC-0011.

## Segurança, autorização e privacidade

1. Todas as rotas desta SPEC devem ficar sob o prefixo administrativo interno
   `/admin/acessorias` e exigir o header `Authorization: Bearer <token>`.
   O token deve ser lido de `ADMIN_API_TOKEN`, configurado por secret manager ou
   variável de ambiente protegida; nunca pode ser persistido, exibido no
   frontend, incluído no OpenAPI ou registrado em logs.
2. Esta instalação possui um único operador. Não haverá tabela de usuários,
   cadastro, IdP, JWT, RBAC ou permissão dinâmica neste milestone. A posse do
   token é a autenticação e autorização suficiente para todas as rotas da SPEC.
   O serviço deve comparar o valor em tempo constante e falhar ao iniciar se o
   token estiver ausente, vazio ou configurado de forma inválida.
3. Token ausente, inválido ou malformado deve retornar `401` com detalhe
   genérico, sem diferenciar contato, empresa ou rota existente. A aplicação
   deve registrar somente categoria sanitizada (`missing_admin_token` ou
   `invalid_admin_token`) e request ID; nunca o token ou seu fingerprint bruto.
4. O ator lógico único deve ser persistido como `admin` em `confirmed_by` ou
   equivalente em toda confirmação/rejeição. A origem da transição deve ser
   `admin_api`, distinguível de `manual_db`; não se usa usuário DigiSac, usuário
   Acessórias, hostname ou usuário do banco como ator substituto.
5. Respostas, logs, métricas e auditoria devem usar IDs estáveis, estados,
   categorias e contagens. Não podem expor telefone, email, payload bruto,
   tokens, headers, conteúdo de conversa nem valores normalizados. Uma política
   futura e separada de acesso a PII é necessária antes de exibir valores
   mascarados ou completos no frontend.
6. O uso inicial recomendado é Swagger autenticado, `curl` ou cliente interno
   na rede/VPN. Se um frontend for criado, ele não pode embutir o token no bundle
   nem persistí-lo em localStorage; deve ser servido no mesmo perímetro e usar
   um backend-for-frontend ou sessão `HttpOnly` que retenha a credencial apenas
   no servidor. Todas as mutações devem registrar request ID/correlation ID
   seguro e aplicar limites de taxa compatíveis com uso humano.

## Recursos de leitura

As leituras existem para sustentar uma fila operacional e um frontend sem dar a
ele acesso ao banco. Elas retornam apenas projeções necessárias à decisão humana.
Nomes podem ser retornados como metadata de exibição; nunca participam de
matching ou seleção automática.

### `GET /admin/acessorias/identity-links`

Lista vínculos e contatos para triagem. Deve aceitar `state` opcional com
`candidate`, `confirmed`, `rejected`, `ambiguous`, `unresolved` ou `conflict`,
`cursor` opaco e `limit` entre 1 e 100. O filtro de estado de resolução pode ser
uma projeção derivada, mas não pode alterar ou criar evidência durante a leitura.

A resposta `200` deve conter `items`, `next_cursor` e contagem limitada quando
ela puder ser calculada sem custo desproporcional. Cada item deve incluir:

- `digisac_contact_external_id`;
- `is_group`, estado de descoberta/resolução atual e `candidate_company_count`;
- para cada vínculo aplicável: `acessorias_company_external_id`, estado, origem,
  horários seguros e nome de exibição permitido;
- resumo de evidência por tipo (`exact_phone`, `exact_email` ou
  `brazil_mobile_variant`), com contagem e horário mais recente, sem valor da
  evidência.

`400` representa filtros/cursor inválidos; `401`, token ausente ou inválido.
Como não há níveis de permissão neste milestone, não há resposta `403` por
RBAC. Um contato sem candidatos não deve ser omitido
quando o filtro pedir `unresolved`.

### `GET /admin/acessorias/contacts/{digisac_contact_external_id}/identity`

Retorna o detalhe de uma decisão. Deve mostrar o contato por ID externo, flag de
grupo, vínculos, transições relevantes, evidências resumidas e a disponibilidade
atual das empresas candidatas. Não deve chamar discovery, hydration ou sync como
efeito colateral.

Retorna `404` somente quando o contato canônico não existir; `200` para um
contato existente sem candidato, inclusive grupo. O formato deve manter IDs
externos como referências de API; IDs locais podem ser usados internamente, mas
não são contrato de cliente.

### `GET /admin/acessorias/companies`

Oferece busca paginada de empresas elegíveis para confirmação manual. Aceita
`query` opcional apenas como filtro de interface, `cursor` e `limit`. Retorna
somente empresas presentes e ativas no diretório, com ID externo e metadata de
exibição permitida. A busca por nome/CNPJ não é matching de identidade e não
pode gerar candidato, confirmação, ranking ou fallback.

## Recursos de mutação

As mutações devem serializar por contato, validar a disponibilidade corrente da
empresa e conservar toda evidência e transição anterior. Repetir uma requisição
com a mesma chave e o mesmo alvo deve retornar o mesmo resultado sem duplicar
transição de auditoria. Uma chave reutilizada com corpo/alvo diferente deve
falhar em `409`.

### `POST /admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/confirm`

Confirma explicitamente a empresa indicada pelo corpo:

```json
{
  "acessorias_company_external_id": "empresa-externa-123",
  "reason": "operator_verified",
  "idempotency_key": "chave-opaca-gerada-pelo-cliente"
}
```

`reason` é uma categoria sanitizada e obrigatória; não deve receber texto livre
que possa conter PII. O servidor obtém `confirmed_at` do seu relógio UTC e o
ator exclusivamente da autenticação administrativa. A confirmação pode promover
um vínculo `candidate` ou criar o vínculo manual solicitado apenas se o contato
e a empresa existirem e a empresa estiver presente/ativa. A ação deve recusar
com `409` uma confirmação concorrente para outra empresa; não pode rejeitá-la
implicitamente nem escolher a empresa mais provável.

Retornos: `200` para replay idempotente e `201` para nova confirmação, ambos
com o vínculo serializado; `400` para corpo inválido; `404` para referências
inexistentes; `409` para conflito de confirmação, chave reutilizada de modo
incompatível ou estado concorrente; `422` para empresa indisponível no diretório.
O handler não pode disparar preparação, mapping, recovery ou Request.

### `POST /admin/acessorias/contacts/{digisac_contact_external_id}/identity-links/{acessorias_company_external_id}/reject`

Rejeita um vínculo existente com corpo contendo `reason` sanitizada e
`idempotency_key`. A operação deve criar uma nova transição auditável e preservar
o vínculo/evidências; hard delete é proibido. Rejeitar um vínculo confirmado
deve ser explicitamente permitido somente como correção administrativa e deve
manter o histórico de confirmação. O resultado nunca promove outra empresa:
após a rejeição, a próxima resolução aplicável continua sujeita às regras
conservadoras de SPEC-0009.

Retornos seguem o contrato de confirmação. Rejeitar uma empresa que não tenha
vínculo com o contato retorna `404`; estado já rejeitado com a mesma chave é
replay idempotente, não nova auditoria.

### `POST /admin/acessorias/contacts/{digisac_contact_external_id}/identity-discovery`

Executa novamente a descoberta determinística da SPEC-0009 para um único
contato, com `idempotency_key`. É opcional para o primeiro frontend, mas é a
única ação administrativa autorizada para atualizar candidatos/evidências sem
rodar SQL. Ela requer que contato e diretório já estejam disponíveis; não chama
providers DigiSac/Acessórias, não realiza backfill e não altera confirmação
válida. Retorna `200` com o resultado de discovery e `409` para chave
incompatível ou concorrência não resolvida.

## Construção e fronteiras de implementação

1. Criar router administrativo separado do webhook e das oito rotas atuais, com
   dependência de autenticação do bearer token aplicada ao router inteiro. A
   publicação no OpenAPI deve marcar as rotas como internas/administrativas e
   declarar o security scheme efetivamente adotado; não deve apresentar essas
   rotas como públicas.
2. Definir schemas Pydantic de entrada/saída explícitos e versões de projeção
   próprias. Handlers devem apenas validar HTTP, propagar actor/request ID e
   traduzir erros sanitizados; transações, locks, idempotência e auditoria ficam
   em um serviço de domínio/repositório.
3. Evoluir a fronteira de identidade da SPEC-0009 para suportar rejeição e
   idempotência por comando, mantendo lock por contato, unicidade do par
   contato–empresa e a proteção contra múltiplos `confirmed`. A migration, se
   necessária, deve ser Alembic aditiva e preservar transições existentes.
4. Implementar consultas de projeção com paginação determinística e cursor
   opaco. A listagem não pode fazer N+1 por contato nem depender de Redis. O
   PostgreSQL continua sendo a única autoridade para vínculo e auditoria.
5. Um frontend, se aprovado, deve ser cliente fino dessa API: fila de
   `candidate`/`ambiguous`/`unresolved`, detalhe com evidência resumida, busca de
   empresa e ações de confirmar/rejeitar com confirmação explícita. Ele não pode
   conter regra de matching, falar com PostgreSQL, armazenar credenciais do
   provider ou decidir recovery de Request.
6. Não incluir esta rota no webhook, IA worker ou worker de Request. O fluxo
   futuro permanece: sync/hydration quando necessário → discovery conservadora
   → revisão administrativa → confirmação → novo ciclo elegível. Recuperação de
   ciclo histórico é uma autorização adicional e controlada.

## Verificação e critérios de aceitação

1. Testes de autenticação devem provar que toda rota retorna `401` sem bearer
   token válido, sem vazar a existência de contatos/empresas, e que o serviço
   falha de forma segura quando `ADMIN_API_TOKEN` não está configurado.
2. Testes HTTP e PostgreSQL descartáveis devem cobrir paginação/cursor, detalhe
   sem candidato, contato de grupo, empresa inativa/ausente, confirmação de
   candidato, confirmação manual válida, conflito entre empresas, rejeição
   auditável e replay idempotente de cada mutação.
3. Devem provar que nomes e filtros de busca não geram matching, que email e
   telefone não aparecem em resposta/log/auditoria, e que uma confirmação não
   cria Request, não chama provider e não altera resolução de ciclo existente.
4. Concorrência deve provar que confirmações simultâneas não deixam dois
   vínculos confirmados; replays concorrentes com a mesma chave convergem para
   uma transição e resposta sem duplicação.
5. A entrega deve atualizar o OpenAPI, a documentação operacional e o índice de
   specs, passar Pyright estrito e a matriz aplicável de SPEC-0004. Doubles não
   comprovam o secret manager, DigiSac, Acessórias ou produção.

## Decisões abertas e bloqueios

O contrato de domínio, endpoints e autenticação está definido para o cenário de
um único operador. O único requisito operacional pendente antes do deploy é
provisionar `ADMIN_API_TOKEN` em secret manager/ambiente protegido e restringir
o serviço à rede autorizada ou VPN. A UI é opcional e só deve começar após a API
autenticada estar verificada.
