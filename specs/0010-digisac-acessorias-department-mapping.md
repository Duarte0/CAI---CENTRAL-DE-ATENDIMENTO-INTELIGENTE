# SPEC-0010 — Mapeamento de departamento DigiSac para Acessórias

- **Status:** implementado localmente pelo issue 0016; evidência descartável, sem provider/produção
- **Versão:** 1.1
- **Prioridade/Fase:** P1 / Milestone D — DigiSac Department → Acessórias Department Mapping
- **Rastreabilidade:** PRD §§4, 5.2, 5.5, 8 e 10; ARCHITECTURE §2.1; `IMPLEMENTATION_PLAN.md` Milestone D; SPEC-0001, SPEC-0003, SPEC-0007 e SPEC-0009
- **Dependências:** SPEC-0001, SPEC-0003, SPEC-0007, SPEC-0008 e SPEC-0009; conclusão do Milestone C e disponibilidade da relação atual `company_departments`

**Evidência de implementação (2026-08-14):** issue 0016 adiciona a migration
Alembic `0018_department_mapping`, regras globais por IDs estáveis, transições
`manual_db` e snapshots append-only de avaliação por ciclo. O runner descartável
passou compileall, Pyright estrito, **177 passed, 56 skipped** offline e
**56 passed, 177 deselected** na etapa PostgreSQL; isso é evidência local
sintética/descartável e não comprova provider, Redis ou produção.

## Objetivo e não objetivos

Definir o contrato persistente que transforma o departamento DigiSac atual de
uma conversa/ciclo com identidade de empresa confirmada em um departamento
Acessórias elegível. O mapeamento é uma configuração auditável, não uma decisão
do modelo nem uma inferência de nome. PostgreSQL deve ser sua única autoridade
durável; Redis não pode decidir, confirmar ou manter a única cópia da regra.

Esta especificação não sincroniza diretórios, resolve identidade, cria Request,
altera a classificação, cria endpoint HTTP/UI, modifica as oito rotas atuais ou
usa `intent_type` como entrada primária. Ela não autoriza matching fuzzy,
escolha automática entre departamentos Acessórias elegíveis ou alteração
retroativa de um resultado terminal.

## Estado de referência e fronteira canônica

O checkout atual preserva o histórico cronológico de atribuições DigiSac, mas
não tem tabela, migration, configuração, worker, API ou teste de mapeamento
departamental. SPEC-0007 é canônica para o diretório Acessórias; SPEC-0008 para
identidade de contato DigiSac; SPEC-0009 para a resolução confirmada de empresa.
Esta SPEC é canônica somente para o mapeamento e para o resultado usado por um
ciclo. A criação externa de Request pertence exclusivamente à SPEC-0011.

## Dados, integridade e ciclo de vida

1. A regra é global: identifica exatamente um ID externo estável de departamento
   DigiSac e um ID externo estável de departamento Acessórias, sem dimensão por
   empresa, `intent_type`, usuário, prompt ou nome. Nome é somente metadata de
   exibição; renomear qualquer departamento não pode quebrar uma regra existente
   e não há fallback automático por nome.
2. PostgreSQL é a única autoridade durável para as regras. Não é permitido
   hardcode em Python, prompt, variável de ambiente ou Redis. Cada departamento
   DigiSac pode ter no máximo uma regra **ativa**; múltiplos departamentos
   DigiSac podem apontar explicitamente para o mesmo departamento Acessórias.
3. A regra deve ter identidade estável, estado ativo/inativo, versão ou
   vigência, origem administrativa, horários, motivo sanitizado e metadata de
   exibição/auditoria suficiente para a operação. Não exige identidade de ator
   que o sistema ainda não possui: a origem inicial pode ser `manual_db` e o ator
   pode ser ausente. Desativação deve preservar a regra e sua auditoria, sem
   hard-delete obrigatório.
4. Um mapeamento só pode ser elegível quando a resolução do ciclo for
   `confirmed` e o departamento Acessórias estiver na relação atual da empresa
   no diretório de SPEC-0007. Ausência, vínculo rejeitado, resolução `ambiguous`
   ou `unresolved`, regra inativa, departamento ausente/inativo ou relação atual
   inválida devem produzir estado persistido e sanitizado, nunca uma escolha
   implícita.
5. A resolução de mapeamento usada por conversa/ciclo deve ser uma entidade ou
   snapshot próprio, com regra/versão, fatos de validação, estado, horário e
   referências de origem. Mudança posterior de diretório ou regra não pode
   reescrever silenciosamente um mapeamento já consumido por ciclo terminal ou
   por Request; uma nova avaliação deve ser registrada separadamente.
6. A evolução deve ser Alembic aditiva. Startup não pode criar nem alterar
   tabelas; downgrade que possa apagar regras, auditoria ou snapshots deve
   recusar explicitamente antes de perda de dados, conforme SPEC-0001.

## Avaliação, falhas e compatibilidade

1. Para uma conversa/ciclo com empresa `confirmed`, a avaliação deve: (a) obter
   o departamento DigiSac atual relevante conforme o contrato do ciclo; (b)
   localizar sua regra ativa global; (c) obter o departamento Acessórias
   mapeado; e (d) validá-lo contra o estado atual de `company_departments` da
   empresa resolvida. Sem regra — inclusive regra desativada — o resultado é
   `unresolved`; com regra cujo departamento não esteja atualmente disponível
   para a empresa, é `invalid`/`unresolved`. Em ambos os casos, a falha deve ser
   explícita, persistida e auditável.
2. A avaliação não pode escolher outro departamento por `intent_type`, IA, nome
   semelhante, primeiro departamento da empresa, departamento global de mesmo
   nome, responsável ou departamento histórico de Request. A classificação IA
   pode ser preservada como contexto auditável, mas não participa da decisão
   primária neste milestone. Histórico de atribuições pode ser evidência, nunca
   regra de precedência; Requests históricos futuros preservam seus próprios
   IDs/snapshots e não são alterados por mudança de regra ou de diretório.
3. A criação, alteração, ativação, inativação e confirmação operacional de uma
   regra devem ser serializáveis e auditáveis. A administração inicial é direta
   no PostgreSQL, por procedimento operacional documentado, sem UI nem endpoint
   HTTP público/admin. Repetição, replay e avaliação concorrente devem convergir
   sem duplicar regras, auditoria ou snapshots.
4. Falha de validação, lock, diretório incompleto ou migration parcial deve
   preservar a última regra e avaliação válidas, registrar motivo sanitizado e
   não alterar classificação concluída. Reprocessamento deve limitar-se à
   empresa, departamento ou ciclo afetado.
5. Logs e métricas devem conter IDs seguros, versão de regra, estado, duração,
   contagens e categoria de falha. Eles não podem conter payload bruto, token,
   header, telefone, email, conteúdo de conversa ou segredo.
6. Este contrato não altera webhook, finalização, API HTTP, contrato IA ou
   histórico de atribuições. Sem mapeamento válido, SPEC-0011 não pode criar
   efeito externo.

## Testes, validação e critérios de aceitação

1. Testes PostgreSQL descartáveis devem provar migration para head, referências,
   uma única regra ativa por departamento DigiSac, ativação/inativação,
   concorrência, auditoria e preservação de snapshot terminal; devem também
   provar que dois departamentos DigiSac podem apontar explicitamente para o
   mesmo departamento Acessórias.
2. Testes de avaliação devem cobrir: regra ativa e departamento disponível para
   empresa `confirmed` (`resolved`); ausência de regra (`unresolved`); regra
   desativada (`unresolved`); regra para departamento indisponível em
   `company_departments` (`invalid`/`unresolved`); resolução ambígua/não
   resolvida; e mudança de diretório após snapshot. Devem provar ainda que o
   rename do departamento DigiSac ou Acessórias não altera a resolução baseada
   nos IDs externos estáveis.
3. Testes de falha e regressão devem provar replay/idempotência e que lock,
   diretório incompleto, repetição e
   rollback parcial não selecionam departamento nem modificam classificação; os
   testes devem confirmar ausência de PII e segredo em logs/estado operacional,
   bem como a inexistência de seleção por nome/fuzzy/IA ou qualquer fallback
   proibido.
4. A implementação deve passar a suíte offline aplicável, Pyright estrito e o
   runner canônico de SPEC-0004. Evidência de provider ou produção não pode ser
   inferida dos doubles.

- Um ciclo com empresa confirmada e uma única regra válida obtém snapshot
  auditável do departamento Acessórias elegível.
- Um ciclo sem confirmação, sem regra ou com fato inválido permanece sem
  mapeamento e não cria Request.
- Alterar uma regra ou a relação atual não reescreve o snapshot histórico nem
  invalida um Request já persistido.

## Decisões registradas, operação e bloqueios restantes

O Product Owner aprovou a governança inicial: chave global
`digisac_department_external_id → acessorias_department_external_id`, sem
precedência entre dimensões adicionais, com unicidade da regra ativa no lado
DigiSac; ciclo de vida ativo/inativo preservado; e origem administrativa
`manual_db` com ator opcional. O procedimento inicial documentado é uma
operação transacional direta no PostgreSQL que registra IDs externos estáveis,
origem, horário, motivo sanitizado e metadata disponível, verifica a unicidade
da regra ativa e conserva o histórico ao desativar. Não há UI nem endpoint neste
milestone.

Portanto, governança, ownership e administração não bloquearam a implementação
do Milestone D. O contrato implementado fornece a administração `manual_db` e a
avaliação por ciclo quando há departamento atual, resolução `confirmed` e
`company_departments` disponível como estado corrente de diretório. Isso não
autoriza criar Request, inferir regras por nomes ou usar `intent_type` como
roteamento.
