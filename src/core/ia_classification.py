"""Pure model-facing IA classification contract.

This boundary owns prompt construction and model-output validation only. It has
no provider, persistence, queue, cycle, or application configuration state.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, cast

from src.core.intents import normalize_intent_type

logger = logging.getLogger(__name__)


def parse_result(result_text: str) -> Dict[str, Any]:
    """Extrai o JSON da resposta do modelo, tolerando markdown/texto solto."""
    try:
        return validate_result(json.loads(result_text))
    except json.JSONDecodeError:
        pass

    # GPT-OSS pode incluir reasoning/tags/markdown antes da resposta final.
    # raw_decode encontra o fim real de cada objeto, respeitando chaves dentro
    # de strings e objetos aninhados (algo que uma regex ``{.*?}`` não faz).
    decoder = json.JSONDecoder()
    expected_fields = {"intent_type", "confidence", "title", "description"}
    for match in re.finditer(r"\{", result_text):
        try:
            candidate, _ = decoder.raw_decode(result_text, match.start())
        except json.JSONDecodeError:
            continue
        if not isinstance(candidate, dict):
            continue
        typed_candidate = cast(dict[str, Any], candidate)
        if not expected_fields.intersection(typed_candidate):
            continue

        logger.warning(
            "Recovered Groq classification JSON from wrapped response; "
            "outcome=wrapped json_offset=%s",
            min(match.start(), 10_000),
        )
        return validate_result(typed_candidate)

    logger.warning(
        "Groq response does not contain a complete classification JSON; "
        "outcome=invalid response_length=%s",
        min(len(result_text), 10_000),
    )
    # Never persist the raw/truncated model response as business data. Raising
    # lets the existing retry/dead-letter policy handle transient generations.
    raise ValueError(
        "Groq response does not contain valid classification JSON")

def validate_result(result: Any) -> Dict[str, Any]:
    """Validate the model contract and neutralize unsupported intents."""
    if not isinstance(result, dict):
        raise ValueError("A resposta JSON da IA deve ser um objeto")
    typed_result = cast(dict[str, Any], result)

    required_fields = {"intent_type", "confidence", "title", "description"}
    missing_fields = required_fields.difference(typed_result)
    if missing_fields:
        raise ValueError(
            "Resposta JSON da IA sem campos obrigatórios: "
            + ", ".join(sorted(missing_fields))
        )
    if (
        not isinstance(typed_result["title"], str)
        or not typed_result["title"].strip()
    ):
        raise ValueError(
            "O título da resposta da IA deve ser texto não vazio")
    if (
        not isinstance(typed_result["description"], str)
        or not typed_result["description"].strip()
    ):
        raise ValueError(
            "A descrição da resposta da IA deve ser texto não vazio")
    confidence = typed_result["confidence"]
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise ValueError(
            "A confiança da resposta da IA deve estar entre 0 e 1")

    raw_intent_type = typed_result.get("intent_type")
    intent_type = normalize_intent_type(raw_intent_type)
    if intent_type is None:
        logger.warning(
            "Unsupported model intent; raw_intent_type=%r "
            "saved_intent_type='other' saved_confidence=0.0",
            raw_intent_type,
        )
        typed_result["intent_type"] = "other"
        typed_result["confidence"] = 0.0
    else:
        typed_result["intent_type"] = intent_type
        logger.info(
            "Intent normalization: raw_intent_type=%r saved_intent_type=%r confidence=%r",
            raw_intent_type,
            intent_type,
            typed_result.get("confidence"),
        )
    return typed_result


def system_prompt() -> str:
    return (
        "Você é um analista de atendimento ao cliente de um escritório de "
        "contabilidade brasileiro. Sua tarefa é ler o trecho de conversa de "
        "um cliente e classificar a intenção principal da mensagem "
        "(ex.: emissão de guias, notas fiscais, folha de pagamento, FGTS, "
        "INSS, impostos, prazos, ou outros temas fiscais/contábeis/"
        "trabalhistas), seguindo rigorosamente as regras e o formato de "
        "saída indicados no prompt do usuário. Responda sempre em português "
        "do Brasil, em um único objeto JSON, sem nenhum texto antes ou "
        "depois, sem markdown e sem explicações adicionais."
    )


def build_prompt(context: str) -> str:
    return f"""Analise esta conversa de atendimento entre um cliente e o escritório de contabilidade.
O texto abaixo contém mensagens cronológicas das duas partes, uma por linha,
prefixadas com "Cliente:" ou "Atendente:".

Para o campo "intent_type", classifique exclusivamente a intenção, necessidade
ou problema expresso pelo CLIENTE. Se não houver qualquer registro do cliente,
use a mensagem do ATENDENTE.

Para os campos "title" e "description":
- O título deve ser conciso mas abrangente, capturando TODOS os assuntos de negócio tratados. Se houver mais de um, separe por " + ". Exemplo: "Emissão de guia + férias com correção de quinquênio".
- A descrição deve ser um resumo estruturado em bullet points, contendo:
  * As solicitações iniciais do cliente (o que ele pediu).
  * As ações tomadas pelo atendente (encaminhamentos, prazos, períodos definidos).
  * Quaisquer correções, ajustes ou pendências identificadas (ex.: erro no quinquênio, documentos faltantes).
  * Se houver prazos ou datas, mencione-os explicitamente.
  * Se o atendente orientou o cliente a fazer algo (ex.: reenviar documento), deixe claro.
- FORMATO OBRIGATÓRIO da descrição: cada bullet point deve começar com o
  caractere "•" seguido de espaço, e terminar com ";" (ponto e vírgula) — exceto
  o último bullet, que termina com "." (ponto final). Nunca use "-" como
  marcador. Exemplo de formato correto:
  "• Cliente solicitou recálculo da guia;
   • Atendente confirmou o envio do documento;{" "}
   • Pendente resposta do cliente sobre o prazo."
- Se não houver absolutamente nenhum conteúdo informativo na conversa (nem do cliente, nem do atendente), use a descrição "conversa vazia ou incompleta". Caso o atendente tenha comunicado algo relevante (ex.: envio de guias, pendência de documento, pedido de reenvio), descreva isso normalmente, mesmo que "intent_type" seja "other"

REGRA DE STATUS (obrigatória): a descrição nunca pode dizer que algo foi
"enviado", "recebido", "confirmado" ou "concluído" a menos que exista uma frase
explícita nesse sentido na conversa. Se o cliente disse "vou enviar", "ainda não
consegui", "não localizei" ou o atendente disse "aguardando" / "fico no
aguardo", isso é PENDENTE — descreva como pendência em aberto, nunca como
concluído. Na dúvida entre "feito" e "pendente", escolha "pendente".

Para o campo "confidence", siga estas regras numeradas:

1. CLASSIFIQUE o caso em uma das três categorias abaixo, com base na clareza do assunto:

   CATEGORIA ALTA (decimal entre 0.85 e 1.0):
   - Há mensagens do cliente com assunto de negócio claro e identificável
   - Não há contradição entre o que o cliente pediu e o que o atendente respondeu
   - Use o topo da faixa (perto de 1.0) só quando não há nenhuma lacuna de
     informação; use 1.0 apenas quando todos os detalhes estiverem
     perfeitamente claros (situação rara)
   - Use a base da faixa (perto de 0.85) quando o assunto é claro mas algum
     detalhe secundário (prazo, valor, período) não foi mencionado

   CATEGORIA MÉDIA (decimal entre 0.50 e 0.84):
   - O assunto principal é identificável, mas há um destes fatores:
     a) Mais de um assunto candidato e não fica claro qual é o principal
     b) Informação relevante contraditória entre cliente e atendente
     c) A conversa depende de contexto anterior não disponível no trecho,
        mas ainda é possível inferir o assunto com razoável segurança

   CATEGORIA BAIXA (decimal entre 0.0 e 0.49):
   - Não há mensagem do cliente com conteúdo (ex.: só "ok", "obrigado", emoji)
     e nenhum contexto anterior disponível
   - O trecho é curto demais ou vago demais para identificar o assunto com
     segurança, mesmo considerando a mensagem do atendente

2. Nunca use 1.0 a menos que todos os detalhes estejam perfeitamente claros
   (situação rara) — na dúvida, prefira um valor levemente abaixo de 1.0

3. Na dúvida entre duas categorias adjacentes, escolha a categoria mais baixa,
   já que o valor de confidence é usado como critério de corte para revisão
   humana

4. O campo "confidence" deve ser um número decimal válido em JSON, com
   exatamente duas casas decimais:
    Correto: 0.85, 0.70, 0.45, 1.0
    Incorreto: .85, 0,85, "0.85", 0. nine, 0 ponto 85

--- INÍCIO DA CONVERSA ---
{context}
--- FIM DA CONVERSA ---

Responda apenas com um objeto JSON no seguinte formato, sem nenhum texto adicional:
{{
  "intent_type": "um de: question, problem, request, complaint, payment, billing, financial, document, protocol, other",
  "confidence": NÚMERO entre 0.0 e 1.0,
  "title": "título curto e abrangente",
  "description": "descrição estruturada e detalhada"
}}

Priorize o ASSUNTO DE NEGÓCIO quando ele estiver claro, mesmo que a fala do
cliente seja apenas uma confirmação ou agradecimento, nesses casos "other"
só deve ser usado se o contexto realmente não revelar nenhum assunto.

Use "payment" para pagamento, confirmação de pagamento, ou quando o cliente
reclama de um valor cobrado mas a mensagem deixa clara a intenção/ação final de
pagar (a intenção de pagar sempre prevalece sobre a reclamação do valor);
"billing" para cobrança, boleto ou valor cobrado quando NÃO há confirmação ou
intenção de pagamento na mesma conversa (ex.: cliente apenas questiona ou
contesta o valor, sem indicar que vai pagar); "financial" para questões
financeiras gerais, como fluxo de caixa, empréstimos ou investimentos;
"document" para envio, reenvio
ou pendência de documento; e "protocol" para protocolo ou andamento de
processo. Se houver pagamento seguido de protocolo, escolha "payment" quando a
ação do cliente foi pagar e "protocol" quando o foco for acompanhar/protocolar.

Use "question" para dúvida, pedido de explicação ou orientação feita pelo cliente;
"problem" para erro, falha ou mau funcionamento relatado pelo cliente; "request" para
solicitação de uma ação feita pelo cliente; "complaint" para reclamação ou insatisfação
do cliente; e "other" somente quando não houver assunto de negócio identificável.
Escolha somente um dos valores listados.

Exemplos:
- "Vou te enviar a taxa" / "Pagando" / "Feito" => payment
- "Esse valor foi cobrado em duplicidade, vou pagar mesmo assim" => payment
  (reclamação + intenção de pagar => a intenção de pagar decide)
- "Esse valor foi cobrado em duplicidade" (sem confirmar pagamento) => billing
- "Preciso que envie novamente o certificado digital" => document
- "Já foi protocolado?" => protocol
- "Obrigado, bom dia" sem outro assunto identificável => other
"""
