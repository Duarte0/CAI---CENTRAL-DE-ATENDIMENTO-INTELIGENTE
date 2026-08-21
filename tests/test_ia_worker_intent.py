import re
from types import SimpleNamespace

import pytest

from src.core.intents import VALID_INTENT_TYPES
from src.core.ia_classification import build_prompt, parse_result
from src.workers.ia_worker import IAWorker


def _worker_without_init() -> IAWorker:
    return object.__new__(IAWorker)


class _FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def test_parse_result_accepts_valid_intent_type():
    result = parse_result(
        '{"intent_type":"problem","confidence":0.94,"title":"Erro",'
        '"description":"Cliente relata um erro."}'
    )

    assert result["intent_type"] == "problem"


def test_parse_result_accepts_financial_intent_type():
    result = parse_result(
        '{"intent_type":"financial","confidence":0.94,"title":"Fluxo de caixa",'
        '"description":"Cliente solicita orientação financeira."}'
    )

    assert result["intent_type"] == "financial"


def test_parse_result_normalizes_unknown_intent_type():
    result = parse_result(
        '{"intent_type":"urgent","confidence":0.8,"title":"Urgência",'
        '"description":"Cliente relata urgência."}'
    )

    assert result["intent_type"] == "other"
    assert result["confidence"] == 0.0


def test_parse_result_normalizes_portuguese_case_and_accents():
    result = parse_result(
        '{"intent_type":"  COBRANÇA ","confidence":0.82,"title":"Cobrança",'
        '"description":"Cliente questiona uma cobrança."}'
    )

    assert result["intent_type"] == "billing"
    assert result["confidence"] == 0.82


def test_parse_result_uses_other_for_invalid_json():
    with pytest.raises(ValueError, match="valid classification JSON"):
        parse_result("resposta sem JSON")


def test_parse_result_rejects_truncated_json_instead_of_persisting_it():
    response = (
        '{"intent_type":"question","confidence":0.95,'
        '"title":"Consulta de impostos","description":"Cliente questiona'
    )

    with pytest.raises(ValueError, match="valid classification JSON"):
        parse_result(response)


def test_parse_result_requires_the_complete_contract():
    with pytest.raises(ValueError, match="campos obrigatórios"):
        parse_result(
            '{"intent_type":"question","confidence":0.9}'
        )


@pytest.mark.asyncio
async def test_analyze_requests_json_and_low_reasoning_effort():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content=(
                        '{"intent_type":"question","confidence":0.9,'
                        '"title":"Impostos","description":"Cliente questiona impostos."}'
                    )
                ),
            )
        ]
    )
    completions = _FakeCompletions(response)
    worker = _worker_without_init()
    worker.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    worker.model = "openai/gpt-oss-120b"
    worker.max_tokens = 1000

    result = await worker._analyze_with_groq("Cliente: Qual a alíquota?", 1)

    assert result["intent_type"] == "question"
    assert completions.kwargs["response_format"] == {"type": "json_object"}
    assert completions.kwargs["extra_body"] == {"reasoning_effort": "low"}


@pytest.mark.asyncio
async def test_analyze_rejects_token_truncation():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="length",
                message=SimpleNamespace(content='{"intent_type":"question"'),
            )
        ]
    )
    worker = _worker_without_init()
    worker.client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions(response))
    )
    worker.model = "openai/gpt-oss-120b"
    worker.max_tokens = 1000

    with pytest.raises(RuntimeError, match="output token limit"):
        await worker._analyze_with_groq("Cliente: Qual a alíquota?", 1)


@pytest.mark.asyncio
async def test_analyze_rejects_empty_response():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=""),
            )
        ]
    )
    worker = _worker_without_init()
    worker.client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions(response))
    )
    worker.model = "openai/gpt-oss-120b"
    worker.max_tokens = 1000

    with pytest.raises(RuntimeError, match="empty classification response"):
        await worker._analyze_with_groq("Cliente: Qual a alíquota?", 1)


def test_parse_result_extracts_json_after_reasoning_tags(caplog):
    reasoning = "PRIVATE_REASONING_SENTINEL_0024"
    title = "PRIVATE_TITLE_SENTINEL_0024"
    description = "PRIVATE_DESCRIPTION_SENTINEL_0024"

    result = parse_result(
        f'<reasoning>{reasoning}</reasoning>\n'
        f'{{"intent_type":"problem","confidence":0.91,"title":"{title}",'
        f'"description":"{description}"}}'
    )

    assert result["intent_type"] == "problem"
    assert result["confidence"] == 0.91
    assert result["title"] == title
    assert "Recovered Groq classification JSON" in caplog.text
    assert "outcome=wrapped" in caplog.text
    assert reasoning not in caplog.text
    assert title not in caplog.text
    assert description not in caplog.text
    assert "raw_response" not in caplog.text


def test_parse_result_invalid_response_logs_only_safe_metadata(caplog):
    sentinel = "PRIVATE_MALFORMED_RESPONSE_SENTINEL_0024"
    response = f"{sentinel}: output incompleto do cliente"

    with pytest.raises(ValueError, match="valid classification JSON"):
        parse_result(response)

    assert (
        "Groq response does not contain a complete classification JSON"
        in caplog.text
    )
    assert "outcome=invalid" in caplog.text
    assert sentinel not in caplog.text
    assert "response_preview" not in caplog.text


def test_parse_result_extracts_nested_json_from_markdown(caplog):
    result = parse_result(
        "Resposta final:\n```json\n"
        '{"result":{"intent_type":"billing","confidence":0.83,'
        '"title":"Cobrança","description":"Cliente questiona o valor."}}\n'
        "```"
    )

    assert result["intent_type"] == "billing"
    assert result["confidence"] == 0.83
    assert result["title"] == "Cobrança"
    assert "Recovered Groq classification JSON" in caplog.text


def test_parse_result_handles_braces_inside_json_strings():
    result = parse_result(
        "<think>comparando campos</think>\n```json\n"
        '{"intent_type":"question","confidence":0.76,'
        '"title":"Uso de {código}","description":"Dúvida sobre o campo."}\n```'
    )

    assert result["intent_type"] == "question"
    assert result["title"] == "Uso de {código}"


def test_prompt_classifies_client_intent_with_both_authors_in_context():
    prompt = build_prompt("Cliente: Preciso de ajuda\nAtendente: Qual sistema?")

    assert 'prefixadas com "Cliente:" ou "Atendente:"' in prompt
    assert "exclusivamente" in prompt
    assert "As ações tomadas pelo atendente" in prompt
    assert '"department"' not in prompt
    assert '"agent"' not in prompt


def test_prompt_intent_taxonomy_matches_shared_canonical_values():
    prompt = build_prompt("Cliente: Preciso de ajuda")
    match = re.search(r'"intent_type": "um de: ([^"]+)"', prompt)

    assert match is not None
    assert tuple(match.group(1).split(", ")) == VALID_INTENT_TYPES


def test_prompt_guides_financial_intent_without_changing_payment_precedence():
    prompt = build_prompt("Cliente: Preciso de orientação")

    assert re.search(r'"financial"\s+para questões\s+financeiras gerais', prompt)
    assert '"payment" para pagamento' in prompt
    assert '"billing" para cobrança' in prompt


def test_payment_and_protocol_example_is_not_instructed_as_other():
    context = (
        "Atendente: Oii Thales, consegui desenrolar o processo da Polo Climatização, "
        "vou te enviar a taxa\nCliente: Boa\nCliente: Pagando\nCliente: Feito\n"
        "Atendente: Perfeito, dando baixa no sistema eu já protocolo"
    )
    prompt = build_prompt(context)

    assert '"payment" para pagamento' in prompt
    assert '"protocol" para protocolo' in prompt
    assert '"other" somente quando não houver assunto de negócio identificável' in prompt
    assert '"Pagando" / "Feito" => payment' in prompt

    # Groq may answer with the Portuguese label; the boundary must preserve the
    # business meaning instead of silently collapsing it to ``other``.
    result = parse_result(
        '{"intent_type":"pagamento","confidence":0.87,'
        '"title":"Pagamento da taxa da Polo Climatização",'
        '"description":"Cliente confirmou o pagamento da taxa."}'
    )
    assert result["intent_type"] == "payment"
    assert result["intent_type"] != "other"
