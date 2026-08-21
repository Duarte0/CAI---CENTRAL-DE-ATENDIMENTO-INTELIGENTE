# SPEC-0007 — Fundação do diretório externo Acessórias

- **Status:** implementado localmente pelo issue 0012; boundary estrutural pelo issue 0034; evidência descartável, sem provider/produção
- **Versão:** 1.1
- **Prioridade/Fase:** P0 / Milestone A — External Directory Foundation
- **Rastreabilidade:** PRD §§3, 4, 5.5, 8 e 10; ARCHITECTURE §2.1 e §§8–9; `IMPLEMENTATION_PLAN.md` Milestone A; SPEC-0001 e SPEC-0004
- **Dependências:** SPEC-0001, SPEC-0004; configuração segura de credencial da Acessórias

**Evidência de implementação (2026-08-14):** issue 0012 adiciona a migration
Alembic `0015_acessorias_directory`, o adaptador tipado, reconciliação
transacional, CLI interno e doubles determinísticos. O runner descartável
passou compileall, Pyright estrito, **143 passed, 36 skipped** offline e
**36 passed, 143 deselected** em PostgreSQL 16; isso não comprova provider,
Redis ou produção.

**Manutenção estrutural (2026-08-20):** issue 0034 moveu o estado e a
admissão genérica do Sliding Window para `src/core/provider_coordination.py`.
O adaptador de Directory continua com escopo local por instância e nenhuma
regra de limite, retry, autenticação, paginação ou publicação foi alterada.

## Objetivo e não objetivos

Definir o primeiro incremento da integração Acessórias: um diretório local,
Alembic-owned e reconciliável de empresas, seus contatos, departamentos e
relacionamentos atuais empresa-departamento. O diretório permite descoberta
posterior; PostgreSQL é a autoridade local durável e Redis **não pode** conter a
autoridade, o estado de sincronização nem a base para identidade.

Esta especificação **não** cria ou atualiza Request, não sincroniza Users, não
persiste contato DigiSac, não faz resolução ou associação de identidade, não
mapeia departamento DigiSac, não altera o contrato de IA e não cria endpoint
HTTP, interface administrativa ou refresh público. Ela não autoriza matching
automático, fuzzy matching ou efeito externo de escrita na Acessórias.

## Estado de referência e fronteira canônica

O checkout atual possui o cliente tipado, configuração, migration, tabelas e
testes da Acessórias implementados pelo issue 0012. A evidência autorizada para
o tenant atual confirma a base
`https://api.acessorias.com`, autenticação HTTP Bearer e os contratos de
leitura registrados nesta especificação. Tokens usados na exploração são
comprometidos: seus valores não podem constar em documentação, fixtures,
código, logs, exemplos ou estado persistido. `src/core/digisac_directory.py`,
`digisac_directory_sync_state` e seus testes demonstram somente um padrão já
implementado para o diretório DigiSac; eles **não são** contrato da API
Acessórias e não podem determinar seus paths, campos, paginação ou credenciais.

Esta é a especificação canônica do diretório Acessórias. SPEC-0001 continua
definindo as regras transversais de banco, migração, retenção e privacidade;
SPEC-0002 não ganha nova superfície HTTP. Os contratos sucessores de contato
DigiSac, resolução de identidade, mapeamento de departamento e Request devem
referenciar esta especificação em vez de duplicar seus recursos ou regras de
sincronização.

## Recursos, dados e integridade

1. A sincronização **deve** tratar empresas, contatos de empresa, departamentos
   e relacionamentos empresa-departamento como recursos distintos, com
   identificador externo opaco e não vazio da Acessórias. Identificadores
   externos **não podem** tornar-se enum de negócio CAI, nem ser reutilizados
   como identificador DigiSac.
2. O schema **deve** persistir, no mínimo, a identidade externa, os atributos
   de exibição fornecidos e seguros, o status bruto da fonte, o estado atual de
   presença na fonte, `source_updated_at` somente quando a fonte o fornecer,
   `synced_at` e o estado
   da execução que os confirmou. Empresa e departamento inativos **devem** ser
   retidos; ausência em uma sincronização completa bem-sucedida **não pode**
   apagar linha histórica.
3. Cada contato de empresa **deve** ser ligado à sua empresa de origem e reter
   separadamente `Celular` e `E-mail` brutos recebidos e suas formas normalizadas,
   quando presentes. A forma normalizada de telefone **deve** conter somente
   dígitos Unicode decimais convertidos para dígitos ASCII; a de email **deve**
   ser o valor sem espaços nas extremidades e com casefold Unicode. Valor bruto
   ausente ou vazio não produz identificador normalizado. Nenhuma dessas formas
   autoriza matching nesta fase.
4. Um relacionamento atual empresa-departamento **deve** ser único pelo par de
   identidades externas e só pode apontar para empresa e departamento duráveis
   existentes. Relações removidas pela fonte devem ficar inativas/não presentes,
   nunca causar remoção física de empresa, departamento ou contato.
5. Chaves únicas, referências e `CHECK` constraints **devem** impedir duplicação
   de uma identidade externa ou de uma relação atual. Registros de execução de
   sync **devem** distinguir início, sucesso completo e falha, com horário,
   contagens seguras e erro sanitizado; não podem armazenar token, cabeçalho de
   autorização ou payload bruto integral.
6. Toda evolução deve ser uma migration Alembic aditiva. Inicialização da
   aplicação **não pode** criar ou alterar essas tabelas. Downgrade que possa
   eliminar diretório ou estado de sync **deve** recusar explicitamente antes de
   perder dados, conforme SPEC-0001.

## Contrato de adaptador e sincronização

1. A implementação **deve** acessar a Acessórias somente por um adaptador
   dedicado. Chamadas a HTTP, autenticação, paginação, parse, classificação de
   falha e conversão para registros de diretório **não podem** ser espalhadas
   por webhook, worker de IA, persistência ou handler HTTP.
2. Para o tenant atual, o adaptador **deve** usar `https://api.acessorias.com`
   e centralizar a autenticação HTTP Bearer obtida exclusivamente de
   configuração segura. Os endpoints de leitura autorizados são:

   - `GET /departments/ListAll`, que retorna objetos com `ID` e `Nome`;
   - `GET /companies/ListAll?contacts&departments&ativa=S&Pagina=N`, cuja
     lista observada contém `ID`, `Identificador`, `Razao`, `Fantasia`,
     `Status`, `Telefone`, `UF`, `ClienteDesde`, `ClienteAte`,
     `DataDoCadastro`, `Honorario`, `ContatosNaEmpresa` e `Departamentos`;
   - `GET /companies/{Identificador}?contacts&departments&registrationData&stateRegistrations`,
     para enriquecimento de uma empresa identificada, com os campos adicionais
     observados `Regime`, `GrupoDeEmpresas` e `InscricoesEstaduais`;
   - `GET /users/ListAll?Status=0&Pagina=1` e `GET /requests/{SolID}` são
     evidência registrada, mas Users e leitura/criação de Requests permanecem
     fora deste milestone.

   `ContatosNaEmpresa` é uma lista — inclusive vazia — e cada contato observado
   tem `Nome`, `E-mail` e `Celular`; múltiplos contatos e valores vazios são
   válidos. `Telefone` da empresa e `Celular` do contato são conceitos distintos.
   `Departamentos` tem `ID`, `Nome`, `RespNome` e `RespEmail`; a relação
   empresa-departamento é estado atual de diretório de primeira classe. O
   adaptador não pode inventar outros nomes de campo ou parâmetro. A consulta
   detalhada pode enriquecer os atributos observados, mas não substitui a
   coleta paginada nem cria uma dependência de cursor incremental.
3. O adaptador deve obter uma visão completa dos quatro recursos autorizados,
   incluindo empresas ativas e inativas. Ele deve conservar `Status` como dado
   bruto do provider e só derivar atividade quando um contrato observado o
   permitir. Se a API exigir seleções separadas para compor ativos e inativos,
   o adaptador deve compor a visão completa sem supor valores textuais de
   `Status`. Uma página inválida, incompleta ou
   repetida, identificador obrigatório ausente, ou referência de relação sem
   pai válido **deve** falhar a execução; dados parciais **não podem** ser
   publicados como uma reconciliação completa.
4. A paginação de empresas deve iniciar em `Pagina=1`, solicitar páginas
   sucessivas enquanto retornarem listas válidas não vazias e terminar somente
   quando uma página válida retornar lista vazia. O adaptador deve detectar
   conteúdo/página repetido que indique loop, impor limite de segurança
   configurável ou interno e falhar — nunca interpretar como fim normal — uma
   página inválida. Um mecanismo mais forte documentado oficialmente pode ser
   adotado na implementação se preservar essa semântica de reconciliação total.
5. A primeira sincronização deve ser uma reconciliação completa paginada. Datas
   recebidas, como `ClienteDesde`, `ClienteAte` e `DataDoCadastro`, podem ser
   persistidas quando úteis, mas não podem ser cursor de delta-sync: não há
   evidência de `updated_at` confiável do provider.
6. Após obter e validar a visão completa, a reconciliação **deve** ocorrer em
   transação PostgreSQL: upsert por identidade externa, atualização de atributos
   e atividade, marcação de ausência/removido e atualização de sucesso da
   execução. Falha, cancelamento ou queda antes do commit **deve** preservar a
   última visão completa bem-sucedida e não pode marcar sucesso.
7. A ausência de recurso só pode atualizar presença/inatividade depois de uma
   visão completa validada e efetivada. Falha parcial não marca ausência;
   recursos e links históricos não são apagados fisicamente; reaparecimento
   reativa ou reconfirma o mesmo registro externo.
8. Repetir a mesma visão completa, repetir uma página já recebida, ou recuperar
   após queda **deve** convergir para o mesmo diretório e estado de sync; não
   pode duplicar contatos, relações nem execuções de sucesso. Apenas uma
   reconciliação Acessórias pode efetivar por vez; a proteção deve ser durável
   ou ter exclusão mútua que sobreviva à concorrência relevante do processo.
9. O sistema **deve** suportar uma sincronização completa inicial, refresh
   periódico e refresh operacional explicitamente invocado, sem criar endpoint
   público. O mecanismo concreto de agendamento/invocação pode ser job ou CLI,
   mas deve usar o mesmo adaptador e as mesmas garantias de transação e lock.
10. Credenciais da Acessórias devem vir exclusivamente de configuração segura.
   O header Bearer deve ser centralizado no adaptador; token,
   valor de configuração e cabeçalho completo **não podem** ser persistidos,
   exibidos em métricas ou logs. Ausência de credencial deve produzir estado
   operacional sanitizado e não alterar o diretório já confirmado.

## Falhas, retry, observabilidade e compatibilidade

1. Timeout, falha de conexão e HTTP `408`, `425`, `429`, `500`, `502`, `503` e
   `504` **devem** ser tratados como transitórios. A integração deve limitar-se
   a no máximo 100 requisições por minuto, com throttling conservador e
   configurável para menor taxa. O adaptador deve fazer número limitado de
   tentativas: em `429` com `Retry-After`, respeita o valor; em `429` sem esse
   header, aplica backoff local limitado. Nenhuma tentativa pode ser infinita
   ou apertar a fonte.
2. HTTP não transitório, erro de autenticação/autorização, payload inválido e
   violação de integridade de dados **devem** encerrar a execução como falha
   sanitizada, preservar a última visão válida e não serem mascarados como
   diretório vazio. Uma execução posterior explicitamente autorizada pode
   tentar novamente.
3. Métricas e logs **devem** permitir correlacionar execução, recurso, página
   ou cursor seguro, tentativas, duração, contagens inseridas/atualizadas/
   inativadas e categoria de falha. Eles **não podem** registrar nomes de
   contato, telefone, email, payloads, URLs com segredo, tokens ou headers de
   autorização.
4. O diretório é compatível apenas com os contratos futuros que o referenciem;
   ele não muda as oito rotas HTTP existentes, a política de consultas internas
   nem o formato de classificação. Dados normalizados nesta fase devem ser
   tratados como evidência técnica, não como confirmação de pessoa ou empresa.

## Testes, validação e critérios de aceitação

1. Testes com double determinístico do adaptador **devem** provar paginação
   `Pagina=N` (início em 1, lista vazia válida, página repetida/loop, limite de
   segurança e página inválida), coleta de todos os quatro recursos,
   empresas inativas, contatos com identificadores brutos/normalizados,
   relações atuais, ausência/removal e reativação posterior.
2. Testes PostgreSQL descartáveis **devem** provar migration para head,
   unicidade, referências, upsert idempotente, repetição concorrente, rollback
   de execução parcialmente falha, preservação da última visão boa e marcação
   correta de sucesso/falha. Eles devem provar que Redis não é necessário como
   autoridade para o diretório.
3. Testes de adaptador **devem** cobrir timeout, conexão, `429` com e sem
   `Retry-After`, `5xx`, autenticação falha, página/payload inválido, limite de
   tentativas e ausência de credencial, verificando que segredo e PII não
   aparecem em logs ou estado persistido.
4. A implementação deve passar a suíte offline aplicável, Pyright estrito para
   novas fronteiras tipadas e o runner canônico de SPEC-0004 quando migration ou
   testes PostgreSQL forem adicionados. Evidência de provider real, Redis ou
   produção deve ser registrada separadamente e não é inferida desses doubles.

Critérios verificáveis de aceitação:

- Uma sincronização completa e válida cria ou reconcilia os quatro recursos em
  PostgreSQL e uma repetição não muda a cardinalidade nem duplica relações.
- Falha antes do commit deixa a última visão completa consultável e não marca
  sucesso; ausência em visão completa posterior inativa sem apagar; retorno da
  fonte reativa o mesmo registro externo.
- Toda identificação de contato preserva valor bruto e normalizado sem acionar
  associação DigiSac-Acessórias, Request, endpoint HTTP ou matching automático.
- Retry transitório é limitado, respeita `Retry-After`, e logs/estado de sync
  permanecem sanitizados.

## Decisões abertas e prontidão

Não há blocker material conhecido para abrir as issues de implementação do
Milestone A. A implementação deve respeitar o contrato de evidência acima e
registrar, em seus próprios testes/doubles, as respostas autorizadas sem
reproduzir credenciais reais.

Continuam deliberadamente fora deste contrato, sem bloquear a primeira issue:
a versão nominal da API; valores textuais completos de `Status`; a forma exata
de seleção de empresas inativas, caso consultas separadas sejam necessárias; e
um mecanismo de snapshot mais forte que a paginação observada. Esses pontos têm
tratamento conservador já definido: compor a visão completa, preservar status
bruto, falhar em parcial/loop e publicar ausência apenas após sucesso total.
Users, Request (inclusive criação), identidade DigiSac e mapeamento de
departamento continuam contratos de milestones posteriores.
