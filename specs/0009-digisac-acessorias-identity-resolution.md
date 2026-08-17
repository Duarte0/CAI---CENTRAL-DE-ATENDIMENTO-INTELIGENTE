# SPEC-0009 — Resolução de identidade DigiSac–Acessórias

- **Status:** implementado localmente pelos issues 0015 e 0026; evidência descartável, sem provider/produção
- **Versão:** 1.2 (issue 0026 conecta a resolução ao limite de preparação do worker)
- **Prioridade/Fase:** P1 / Milestone C — DigiSac ↔ Acessórias Identity Resolution
- **Rastreabilidade:** PRD §§4, 5.5, 8 e 10; ARCHITECTURE §2.1; `IMPLEMENTATION_PLAN.md` Milestone C; SPEC-0001, SPEC-0004, SPEC-0007 e SPEC-0008
- **Dependências:** SPEC-0001, SPEC-0004, SPEC-0007 e SPEC-0008 implementadas; nenhum blocker de decisão de produto permanece neste milestone

**Evidência de implementação (2026-08-14):** issue 0015 adiciona a migration
Alembic `0017_digisac_acessorias_identity`, evidência técnica com fingerprint
sanitizado, vínculos muitos-para-muitos, transições auditáveis, resolução
imutável por ciclo e confirmação controlada `manual_db`. A implementação
também acrescenta a forma normalizada de email ao contato DigiSac, necessária
para o matching exato previsto nesta SPEC. O runner descartável passou
compileall, Pyright estrito, **175 passed, 48 skipped** offline e **48 passed,
175 deselected** na etapa PostgreSQL;
isso é evidência local sintética/descartável e não comprova provider, Redis ou
produção.

**Integração de preparação (2026-08-17):** issue 0026 chama
`resolve_cycle_identity()` somente com o contato de ticket persistido e exige o
estado `confirmed` antes de avaliar departamento ou criar Request. Candidatos,
grupos sem vínculo confirmado, ambiguidades, conflitos e resultados não
resolvidos continuam bloqueando a cadeia sem promoção automática. A cobertura
adicional está registrada na execução descartável do issue 0026; nenhum
provider ou ambiente de produção foi usado.

## Objetivo e não objetivos

Definir resolução conservadora entre uma identidade DigiSac durável e empresas
do diretório Acessórias. A resolução **deve** separar: evidência técnica
(`evidence`/`match`), empresa candidata, vínculo explicitamente confirmado e
resolução usada por conversa/ciclo. PostgreSQL é a autoridade de cada registro;
Redis **não pode** decidir, confirmar ou armazenar a única cópia de resolução.

Esta especificação **não** sincroniza diretórios, cria Request, mapeia
departamento, altera IA, altera as oito rotas HTTP, cria UI, endpoint HTTP ou
API pública de confirmação. Ela **não** usa fuzzy name matching, `idFromService`,
nome ou número de grupo como evidência automática. Nome, `alternativeName`, nome
da empresa e nome do contato podem ser preservados somente como metadata de
diagnóstico ou auxílio humano.

## Estado de referência e fronteira canônica

SPEC-0007 é canônica para empresa/contato/departamento Acessórias e SPEC-0008 é
canônica para contato DigiSac e suas formas técnicas normalizadas. Esta SPEC é
canônica apenas para ligação e seus resultados. SPEC futura de departamento
consumirá a resolução persistida válida sem redefinir matching; SPEC-0011 não
pode criar Request a partir de estado sem empresa única confirmada.

## Dados, estados e integridade

1. Evidência técnica deve apontar para um contato DigiSac existente e um
   contato/empresa Acessórias existente, registrar tipo, valores normalizados
   usados (ou hash seguro quando a política de exposição o exigir), origem,
   horário e versão/regra. Tipos iniciais incluem `exact_phone`,
   `brazil_mobile_variant` e `exact_email`. Evidência nunca confirma empresa
   por si só.
2. A descoberta agrupa evidências por **empresa distinta**, não por contato
   Acessórias. Vários contatos da mesma empresa encontrados pela mesma regra
   produzem uma única empresa candidata e preservam todas as evidências que a
   sustentam. Evidências independentes para a mesma empresa podem preservar
   ranking e diagnóstico, mas não podem gerar score arbitrário nem promoção
   automática a `confirmed`. Se evidências independentes apontarem para empresas
   distintas, cada empresa permanece candidata e o resultado da descoberta é
   `ambiguous`.
3. Vínculo contato DigiSac–empresa deve ser entidade própria, única pelo par
   `digisac_contact_id + acessorias_company_id`, com estado `candidate`,
   `confirmed` ou `rejected`, origem e auditoria de transição/referências de
   evidência. Um contato pode estar ligado a múltiplas empresas; em particular,
   não pode haver `UNIQUE(digisac_contact_id)`.
4. A resolução de conversa/ciclo deve ser entidade distinta e registrar estado
   `confirmed`, `ambiguous`, `unresolved` ou `conflict`, origem, horário e,
   quando houver empresa única, o vínculo confirmado aplicado. `confirmed` é a
   resolução efetivamente resolvida; `ambiguous`, `unresolved` e `conflict`
   bloqueiam toda automação futura que exija empresa única.
5. `candidate` representa descoberta técnica. `confirmed` exige confirmação
   explícita. `rejected` exige motivo sanitizado e auditável. `ambiguous` é
   descoberta em mais de uma empresa distinta; `unresolved` é ausência de
   candidato ou de vínculo confirmado aplicável; `conflict` é estado operacional
   inválido que impede escolha arbitrária. Nenhum desses estados cria Request.
6. Referências, constraints e transições devem impedir evidência órfã,
   duplicação do par de vínculo, confirmação automática e transição impossível.
   Correção administrativa deve preservar a auditoria: um vínculo confirmado
   pode ser rejeitado ou substituído por transição auditável, sem hard-delete
   silencioso de vínculo, evidência ou decisão anterior. A evolução deve ser
   Alembic aditiva; downgrade destrutivo deve recusar-se antes de perda de dados,
   conforme SPEC-0001.

## Descoberta automática conservadora

1. Somente contato DigiSac não-grupo e contato Acessórias com identificador
   normalizado não vazio podem participar da descoberta. A normalização de email
   é trim e casefold Unicode; telefone contém somente dígitos ASCII derivados de
   dígitos Unicode decimais, conforme as foundations.
2. Igualdade exata de telefone normalizado gera evidência `exact_phone`.
   Igualdade exata de email normalizado gera evidência `exact_email`. Para cada
   regra, zero empresas distintas resulta em `unresolved`, exatamente uma em
   `candidate` e mais de uma em `ambiguous`. Nenhum caso é `confirmed`
   automaticamente.
3. A variante móvel brasileira gera evidência `brazil_mobile_variant` e não é
   transformação genérica de telefone. Depois da comparação exata, ela só pode
   ser aplicada quando ambos os números forem estruturalmente válidos e
   deterministicamente interpretáveis como Brasil (`55`), tiverem o mesmo DDD
   de dois dígitos e um tiver oito dígitos locais enquanto o outro tiver nove.
   O número de nove dígitos deve diferir exclusivamente por um `9` adicional na
   posição inicial prevista da variante móvel brasileira; ao removê-lo, os oito
   dígitos restantes devem ser exatamente o número local de oito dígitos.
   Nenhuma outra inserção, remoção, substituição ou comparação fuzzy é permitida.
   DDDs diferentes, telefone estrangeiro e forma estruturalmente inválida não
   usam esta regra. Seu resultado é: zero empresas `unresolved`, uma empresa
   distinta `candidate`, mais de uma `ambiguous`; nunca `confirmed` automático.
4. Nome, `alternativeName`, nome de empresa e nome de contato não geram
   evidência, candidato ou confirmação automática neste milestone. `idFromService`
   também não é chave de matching.
5. Para `is_group = true`, não executar phone matching automático, name matching
   automático nem usar o número do grupo como telefone. Sem vínculo confirmado
   explícito, a resolução é `unresolved`; com exatamente um vínculo confirmado
   aplicável, é `confirmed` para esse vínculo.

## Confirmação, precedência e concorrência

1. Um único vínculo explicitamente `confirmed` aplicável ao contato tem
   precedência sobre toda descoberta automática: a resolução deve usá-lo, mesmo
   que evidências apontem para empresa diferente. Discovery não pode substituí-lo
   silenciosamente.
2. Mais de um vínculo `confirmed` concorrente onde a resolução exige empresa
   única é corrupção/conflito operacional. A resolução deve ser `conflict`, não
   escolher empresa e bloquear automação dependente até correção auditável.
3. Ausente vínculo confirmado único, discovery pode produzir vínculos
   `candidate`/`rejected` e resolução `unresolved` ou `ambiguous`, mas não pode
   promover combinação de `exact_phone`, `exact_email` ou variante brasileira a
   confirmação.
4. Reexecução de matching, replay de sync e concorrência devem convergir para a
   mesma evidência, vínculo e resultado sem duplicar registros/auditoria nem
   rebaixar ou substituir confirmação manual válida. Alteração posterior de
   diretório ou vínculo não pode reescrever silenciosamente resolução já
   persistida de ciclo terminal.

## Procedimento administrativo inicial

O mecanismo inicial autorizado é operação direta, controlada e auditável no
PostgreSQL; não há UI, endpoint HTTP ou API pública neste milestone. O runbook
de confirmação deve executar transação controlada que:

1. recebe explicitamente o ID do contato DigiSac e a identidade externa/local
   correspondente da empresa Acessórias;
2. valida que ambos existem e que o par de vínculo é o alvo solicitado;
3. grava ou transiciona somente esse par para `confirmed`, com
   `confirmation_source = manual_db` (ou equivalente) e `confirmed_at`
   obrigatório;
4. registra `confirmed_by` somente quando a arquitetura fornecer nesse contexto
   uma identidade administrativa confiável. Sem ela, o campo permanece nulo ou
   indisponível conforme o schema; não usar usuário da conversa DigiSac, usuário
   Acessórias, usuário do banco ou hostname como substituto inventado;
5. verifica antes de concluir que a confirmação não deixou múltiplos vínculos
   confirmados concorrentes para uma resolução de empresa única; se deixou,
   falha e preserva estado para correção operacional; e
6. conserva evidência e transições anteriores. A correção de confirmação errada
   deve ser nova transição auditável para `rejected` ou vínculo substituto, nunca
   hard-delete silencioso.

## Privacidade, observabilidade e compatibilidade

1. Logs e métricas devem identificar IDs seguros, regra, estado, contagens e
   categoria de falha, mas não podem conter nome, telefone, email, payload bruto,
   token ou header. Persistência de identificador normalizado segue a retenção
   aprovada de SPEC-0001; exposição futura exige contrato de privacidade separado.
2. Falha de matching, migração, diretório incompleto ou lock concorrente deve
   deixar estado recuperável/auditável e não pode alterar classificação terminal.
   Reprocessamento deve ser idempotente e limitado ao contato/ciclo afetado.
3. Esta resolução não altera contrato HTTP, finalização, classificação,
   assignment history ou diretórios canônicos. Department mapping e Request
   devem exigir resolução persistida `confirmed` válida em suas próprias specs.

## Testes, validação e critérios de aceitação

1. Testes PostgreSQL descartáveis devem provar migration para head,
   cardinalidade muitos-para-muitos, unicidade do par, evidência idempotente,
   concorrência, transições/auditoria e preservação da resolução terminal.
2. Testes de matching devem cobrir: telefone exato único para uma empresa como
   `candidate`; telefone exato compartilhado por duas empresas como `ambiguous`;
   telefone repetido em dois contatos da mesma empresa como uma só candidata;
   email exato único como `candidate`; email compartilhado como `ambiguous`;
   variante brasileira válida 8↔9 como `candidate`; variante com DDD diferente,
   diferença adicional além do `9` ou telefone estrangeiro sem match por variante.
3. Testes devem cobrir grupo sem vínculo confirmado como `unresolved`, grupo com
   vínculo confirmado como resolução `confirmed`, exatamente um vínculo
   confirmado como resolução `confirmed`, e vínculo confirmado mais discovery
   divergente preservando a precedência do vínculo confirmado. Múltiplos vínculos
   confirmados concorrentes devem resultar em `conflict` operacional.
4. Testes do procedimento manual devem provar `manual_db`/origem equivalente,
   `confirmed_at`, `confirmed_by` opcional sem ator confiável, correção auditável
   e replay/idempotência. Testes de falha devem provar que diretório incompleto,
   dado inválido ou falha transacional não confirma empresa nem modifica
   classificação; também devem verificar ausência de PII/segredo em logs e
   estado operacional.
5. A implementação deve passar suíte offline aplicável, Pyright estrito e o
   runner de SPEC-0004. Dado real de provider ou produção não é inferido de
   doubles.

## Decisões abertas e bloqueios

Não permanece blocker material de produto para a decomposição de implementação
do Milestone C. A regra de variante móvel brasileira e a semântica de ator
manual estão aprovadas acima. A ausência de sistema de identidade administrativa
não bloqueia o milestone: `manual_db` e `confirmed_at` são obrigatórios para
confirmação manual, enquanto `confirmed_by` é opcional até existir identidade
confiável. A implementação continua dependente das foundations declaradas e
não autoriza fuzzy matching, confirmação automática, Request ou mapeamento de
departamento.
