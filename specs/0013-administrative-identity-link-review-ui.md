# SPEC-0013 — Interface web administrativa para conciliação de identidade

- **Status:** implementado localmente nos issues 0042–0044; aceitação de produção permanece separada
- **Versão:** 1.5
- **Prioridade/Fase:** P1 / Milestone C.2 — operação administrativa de identidade
- **Rastreabilidade:** SPEC-0008, SPEC-0009, SPEC-0012, issues 0042–0044 e `IMPLEMENTATION_PLAN.md`
- **Dependências:** SPEC-0012 implementada; API administrativa autenticada verificada; `ADMIN_API_TOKEN`, `ADMIN_UI_PASSWORD` e `ADMIN_SESSION_SECRET` provisionados com segurança

## Evidência do slice implementado

O issue 0042 implementa a fundação FastAPI da UI: configuração validada para
`ADMIN_UI_PASSWORD` e `ADMIN_SESSION_SECRET`, formulário local de login,
logout repetível, shell protegido em `/admin/acessorias/ui` e sessão de cookie
assinada por `itsdangerous` com SHA-256. A sessão guarda somente um marcador autenticado, a
versão e o vencimento absoluto de 60 minutos; usa `HttpOnly`, `SameSite=Strict`
e `Secure` em produção, sem renovação durante leituras. Login, logout e shell
usam `Cache-Control: no-store` e não entram no OpenAPI.

O boundary `AdminUIContext` autentica a sessão no processo FastAPI e oferece o
acesso server-side aos serviços já existentes da SPEC-0012, preservando as
projeções sanitizadas sem proxy HTTP, acesso do navegador ao PostgreSQL/Redis
ou exposição do `ADMIN_API_TOKEN`. Não há migration nem escrita em estado de
identidade neste slice.

O issue 0043 substitui o conteúdo provisório pelo primeiro viewport responsivo
com fila filtrada por `candidate`, `ambiguous` e `unresolved`, paginação opaca,
detalhe de contato e busca display-only de empresas presentes e ativas. Os
paths BFF sob `/admin/acessorias/ui/api/` usam somente a sessão assinada,
reutilizam as projeções e cursores da SPEC-0012, retornam `no-store` e não
expõem o token Bearer, valores de contato, storage do navegador ou recursos
externos. Loading, vazio, expiração, ausência, rate limit, timeout, rede e
respostas fora de ordem têm estados seguros no cliente.

O issue 0044 adiciona os três paths BFF de ação sob
`/admin/acessorias/ui/api/`, autenticados pela mesma sessão e sem expor o
`ADMIN_API_TOKEN`: confirmação de um alvo presente/ativo com a razão fixa
`operator_verified`, rejeição de um vínculo selecionado com a razão fixa
`operator_rejected` e discovery determinístico do contato selecionado. O
cliente gera a chave idempotente somente no estado transitório da ação, mantém
a mesma chave quando o operador solicita retry após resultado incerto, bloqueia
cliques concorrentes, exige confirmação explícita e recarrega fila/detalhe após
sucesso ou replay. Os comandos continuam no boundary PostgreSQL da SPEC-0012;
não há migration, provider, Redis ou alteração de resolução histórica.

## Objetivo

Definir uma interface web interna, simples e responsiva, para que um operador
revise contatos DigiSac, consulte evidências resumidas e confirme ou rejeite o
vínculo com uma empresa Acessórias sem usar Swagger, `curl` ou PostgreSQL
diretamente.

A interface será um cliente fino da API da SPEC-0012. Matching, discovery,
auditoria, idempotência, resolução de conflitos e regras de identidade
continuam pertencendo ao backend.

## Não objetivos

Esta SPEC não:

- cria nova regra de matching ou ranking de empresas;
- edita nome, telefone, email, CNPJ ou dados do diretório;
- acessa PostgreSQL ou Redis pelo navegador;
- chama DigiSac ou Acessórias diretamente pelo navegador;
- cria Request, prepara Request ou recupera ciclos históricos;
- cria usuários, IdP, JWT, RBAC ou cadastro de operadores;
- substitui a API administrativa da SPEC-0012;
- persiste o `ADMIN_API_TOKEN` no navegador.

## Decisão de stack

Para o primeiro operador e o fluxo limitado desta SPEC, a implementação deve
usar os recursos já presentes no serviço FastAPI:

- página HTML servida pelo próprio FastAPI;
- CSS local, com tokens de cor, espaçamento, tipografia e estados definidos no
  próprio projeto;
- JavaScript modular sem React, Vite ou outro bundler obrigatório;
- `fetch` para consumir as rotas da SPEC-0012;
- `Jinja2` somente se forem necessários valores de configuração ou textos
  renderizados no servidor;
- nenhum CDN, fonte externa, analytics ou dependência remota necessária para a
  tela funcionar.

React/Vite só deve ser introduzido por uma nova decisão se o escopo crescer
para múltiplas telas, navegação complexa ou estado compartilhado relevante.

## Superfície e fluxo principal

A página deve ficar na seguinte rota interna:

```text
GET /admin/acessorias/ui
```

Esta é a única rota de disponibilização da UI. Não será criada uma camada de
rede/VPN adicional; a proteção de acesso é o perímetro interno já exigido pela
SPEC-0012.

O primeiro viewport deve conter:

1. cabeçalho com o nome da operação, estado da conexão e ação de atualizar;
2. fila de contatos filtrável por `candidate`, `ambiguous` e `unresolved`;
3. detalhe do contato selecionado;
4. lista de vínculos, estado e resumo de evidências permitidas;
5. busca de empresas presentes e ativas;
6. ação explícita de confirmar, rejeitar ou executar redescoberta.

O fluxo de confirmação deve ser:

```text
selecionar contato → revisar evidência resumida → selecionar empresa
→ confirmar ação e reason → POST idempotente → atualizar fila e detalhe
```

A confirmação deve exigir uma segunda ação explícita, como modal ou painel de
revisão. O botão deve ficar desabilitado durante a requisição e a interface
deve preservar a mesma `idempotency_key` se o operador solicitar retry de uma
requisição cujo resultado ficou incerto.

## Contrato com a API

A interface deve consumir somente:

- `GET /admin/acessorias/identity-links` para a fila e paginação;
- `GET /admin/acessorias/contacts/{id}/identity` para o detalhe;
- `GET /admin/acessorias/companies` para a busca display-only;
- `POST /admin/acessorias/contacts/{id}/identity-links/confirm`;
- `POST /admin/acessorias/contacts/{id}/identity-links/{company_id}/reject`;
- `POST /admin/acessorias/contacts/{id}/identity-discovery`.

A interface não deve reconstruir projeções, interpretar valores de evidência,
calcular matching ou inferir estado fora dos campos recebidos.

Para confirmação, a interface deve enviar a categoria fixa `operator_verified`
e gerar uma chave opaca nova por operação. Para rejeição, deve enviar a
categoria fixa `operator_rejected`. Não deve enviar `confirmed_at`,
`confirmed_by`, telefone, email ou texto livre com PII.

## Autenticação e segurança

O navegador não pode receber o `ADMIN_API_TOKEN` em bundle, HTML, URL,
`localStorage`, `sessionStorage`, IndexedDB, logs ou telemetria.

A decisão é usar BFF in-process com sessão de cookie assinada. A UI e a API
administrativa vivem no mesmo processo FastAPI; as rotas da UI chamam o
roteador/serviço administrativo internamente, sem proxy HTTP separado e sem
novo serviço. O token permanece exclusivamente no servidor.

A sessão do operador deve ser:

- `HttpOnly`;
- `Secure` em produção;
- `SameSite=Strict`;
- assinada por `starlette.SessionMiddleware` ou `itsdangerous`;
- válida por 60 minutos a partir do login;
- encerrada sem renovação automática por atividade (sem sliding window).

Não haverá usuário, RBAC ou IdP nesta etapa. O contrato deve prever logout,
ausência de cache da página e resposta genérica para credencial inválida. A
aplicação deve manter o perímetro interno/VPN definido na SPEC-0012.

### Bootstrap de sessão administrativa

A sessão é iniciada por uma única credencial de operador, sem cadastro de
usuário:

- `POST /admin/acessorias/login` recebe a senha do operador e compara seu valor
  com `ADMIN_UI_PASSWORD` usando comparação segura;
- quando válida, cria a sessão assinada e redireciona para
  `/admin/acessorias/ui`;
- quando inválida, retorna erro genérico sem diferenciar credencial incorreta,
  rota ou existência de operador;
- a chave de assinatura vive em `ADMIN_SESSION_SECRET`, provisionada com o
  mesmo cuidado de `ADMIN_API_TOKEN`;
- `ADMIN_UI_PASSWORD` e `ADMIN_SESSION_SECRET` nunca aparecem em logs,
  respostas, HTML, JavaScript, métricas ou cache;
- a proteção CSRF adotada nesta versão é `SameSite=Strict`; nenhum token CSRF
  adicional é emitido.

Não há múltiplos operadores, recuperação de senha ou rotação automática de
credencial nesta versão. O formulário e a resposta de login devem usar
`Cache-Control: no-store`, e a senha só pode ser transmitida por conexão
protegida no perímetro autorizado.

Não devem existir chamadas de terceiros, recursos remotos ou conteúdo que
permita exfiltração do token. Respostas da UI, mensagens de erro e logs devem
continuar sem telefone, email, payload de conversa, token ou valor bruto de
evidência.

## Estados e erros

A interface deve tratar explicitamente:

- `401`: sessão expirada ou credencial inválida; limpar a sessão e solicitar
  autenticação novamente;
- `404`: contato ou empresa não encontrada; atualizar a projeção;
- `409`: conflito de confirmação ou replay incompatível; recarregar o detalhe
  antes de permitir nova decisão;
- `422`: empresa indisponível no diretório; removê-la da seleção;
- `429`, timeout ou erro de rede: preservar a chave da operação e oferecer
  retry seguro, sem gerar uma segunda decisão automaticamente;
- resposta vazia: mostrar estado de fila vazia, sem inventar candidatos.

Após confirmação ou rejeição bem-sucedida, a tela deve recarregar o detalhe e
a fila. Uma resposta de replay idempotente deve ser apresentada como sucesso
já aplicado, sem criar nova transição visualmente.

## Privacidade e acessibilidade

A UI deve mostrar somente os campos já autorizados pela SPEC-0012: IDs
externos, nomes de exibição permitidos, estados, contagens, categorias de
evidência e horários seguros. Não deve exibir valor de telefone, email ou
evidência.

A página deve funcionar em desktop e em viewport estreito, ter navegação por
teclado, foco visível, rótulos associados aos controles, mensagens de erro
anunciáveis e contraste adequado. Estados de carregamento, vazio, erro e
sucesso precisam ser distinguíveis sem depender apenas de cor.

O visual deve priorizar uma fila operacional legível: fundo neutro, um painel
de detalhe claro, hierarquia tipográfica consistente, poucos elementos
decorativos e destaque visual reservado para ações destrutivas ou de
confirmação.

## Compatibilidade e deployment

A UI deve ser empacotada na mesma imagem e no mesmo serviço da API, sem novo
container obrigatório. A publicação deve ocorrer somente após a migration e a
API da SPEC-0012 estarem disponíveis.

A rota da UI não deve ser publicada como API pública. O OpenAPI deve continuar
descrevendo a API administrativa, enquanto a página pode ser documentada em
README/guia operacional separado.

## Critérios de aceitação

1. O operador consegue autenticar-se sem que o token seja exposto ao
   JavaScript persistente ou a logs, usando BFF in-process e sessão assinada
   `HttpOnly`.
2. A fila carrega paginação e filtros da API sem duplicar regra de matching.
3. O detalhe mostra contato sem candidato, contato de grupo e contato com
   múltiplos candidatos sem inventar estado.
4. A seleção de empresa usa somente empresas presentes e ativas retornadas pela
   API.
5. Confirmação, rejeição e redescoberta exigem ação explícita, enviam uma chave
   idempotente e refletem o resultado ou replay sem duplicar ação. Confirmação
   usa `operator_verified` e rejeição usa `operator_rejected`.
6. `401`, `404`, `409`, `422`, timeout e resposta vazia têm tratamento visível
   e seguro.
7. Nenhum teste ou inspeção do navegador encontra telefone, email, evidência
   bruta, token, payload de conversa ou credencial de provider.
8. A tela é utilizável por teclado, em viewport estreito e sem recursos
   externos.
9. Testes de integração cobrem o fluxo de leitura, confirmação, conflito,
   rejeição, discovery, expiração de sessão e replay idempotente.
10. QA visual em navegador cobre carregamento, fila vazia, erro, modal de
    confirmação, sucesso e layout responsivo.
11. Uma sessão expira exatamente após 60 minutos sem renovação automática e a
    próxima chamada é tratada como `401`.

## Decomposição futura

Uma issue de implementação deve separar, no mínimo:

1. shell FastAPI, sessão/BFF e política de segurança;
2. página, tokens visuais, fila, detalhe e busca de empresas;
3. ações de confirmação, rejeição e discovery com estados de erro/retry;
4. testes HTTP, segurança, acessibilidade e QA visual no navegador;
5. documentação operacional e evidência de build.

Esta SPEC autoriza a criação e decomposição das issues acima. A implementação
continua condicionada à aprovação das issues, execução do build e verificação
dos critérios de aceite desta SPEC.

## Decisões de desenho registradas

As decisões desta versão fecham os pontos anteriormente abertos:

| Decisão | Escolha |
| --- | --- |
| BFF versus sessão | BFF in-process no mesmo FastAPI, com sessão `HttpOnly`, `Secure` em produção e `SameSite=Strict` |
| Razão de confirmação | `operator_verified` |
| Razão de rejeição | `operator_rejected` |
| Expiração da sessão | Fixa em 60 minutos desde o login, sem sliding window |
| Rota da UI | Somente `/admin/acessorias/ui`, sem camada de rede adicional além do perímetro da SPEC-0012 |

Estas decisões estão aprovadas para a decomposição em issues. Elas não
autorizam implementação fora das issues aprovadas nem substituem a verificação
do build e dos critérios de aceite desta SPEC.
