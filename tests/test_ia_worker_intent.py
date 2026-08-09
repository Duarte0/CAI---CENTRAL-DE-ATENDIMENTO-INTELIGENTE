import pytest
from types import SimpleNamespace

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
    result = _worker_without_init()._parse_result(
        '{"intent_type":"problem","confidence":0.94,"title":"Erro",'
        '"description":"Cliente relata um erro."}'
    )

    assert result["intent_type"] == "problem"


def test_parse_result_normalizes_unknown_intent_type():
    result = _worker_without_init()._parse_result(
        '{"intent_type":"urgent","confidence":0.8,"title":"Urgência",'
        '"description":"Cliente relata urgência."}'
    )

    assert result["intent_type"] == "other"
    assert result["confidence"] == 0.0


def test_parse_result_normalizes_portuguese_case_and_accents():
    result = _worker_without_init()._parse_result(
        '{"intent_type":"  COBRANÇA ","confidence":0.82,"title":"Cobrança",'
        '"description":"Cliente questiona uma cobrança."}'
    )

    assert result["intent_type"] == "billing"
    assert result["confidence"] == 0.82


def test_parse_result_uses_other_for_invalid_json():
    with pytest.raises(ValueError, match="valid classification JSON"):
        _worker_without_init()._parse_result("resposta sem JSON")


def test_parse_result_rejects_truncated_json_instead_of_persisting_it():
    response = (
        '{"intent_type":"question","confidence":0.95,'
        '"title":"Consulta de impostos","description":"Cliente questiona'
    )

    with pytest.raises(ValueError, match="valid classification JSON"):
        _worker_without_init()._parse_result(response)


def test_parse_result_requires_the_complete_contract():
    with pytest.raises(ValueError, match="campos obrigatórios"):
        _worker_without_init()._parse_result(
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


def test_parse_result_extracts_json_after_reasoning_tags(caplog):
    result = _worker_without_init()._parse_result(
        '<reasoning>{"step":"analisar { contexto }"}</reasoning>\n'
        '{"intent_type":"problem","confidence":0.91,'
        '"title":"Erro no sistema","description":"Falha ao emitir a guia."}'
    )

    assert result["intent_type"] == "problem"
    assert result["confidence"] == 0.91
    assert result["title"] == "Erro no sistema"
    assert "Recovered Groq classification JSON" in caplog.text


def test_parse_result_extracts_nested_json_from_markdown(caplog):
    result = _worker_without_init()._parse_result(
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
    result = _worker_without_init()._parse_result(
        "<think>comparando campos</think>\n```json\n"
        '{"intent_type":"question","confidence":0.76,'
        '"title":"Uso de {código}","description":"Dúvida sobre o campo."}\n```'
    )

    assert result["intent_type"] == "question"
    assert result["title"] == "Uso de {código}"


def test_prompt_classifies_client_intent_with_both_authors_in_context():
    prompt = _worker_without_init()._build_prompt("Cliente: Preciso de ajuda\nAtendente: Qual sistema?")

    assert 'prefixadas com "Cliente:" ou "Atendente:"' in prompt
    assert "exclusivamente" in prompt
    assert "As ações tomadas pelo atendente" in prompt
    assert '"department"' not in prompt
    assert '"agent"' not in prompt


def test_payment_and_protocol_example_is_not_instructed_as_other():
    context = (
        "Atendente: Oii Thales, consegui desenrolar o processo da Polo Climatização, "
        "vou te enviar a taxa\nCliente: Boa\nCliente: Pagando\nCliente: Feito\n"
        "Atendente: Perfeito, dando baixa no sistema eu já protocolo"
    )
    prompt = _worker_without_init()._build_prompt(context)

    assert '"payment" para pagamento' in prompt
    assert '"protocol" para protocolo' in prompt
    assert '"other" somente quando não houver assunto de negócio identificável' in prompt
    assert '"Pagando" / "Feito" => payment' in prompt

    # Groq may answer with the Portuguese label; the boundary must preserve the
    # business meaning instead of silently collapsing it to ``other``.
    result = _worker_without_init()._parse_result(
        '{"intent_type":"pagamento","confidence":0.87,'
        '"title":"Pagamento da taxa da Polo Climatização",'
        '"description":"Cliente confirmou o pagamento da taxa."}'
    )
    assert result["intent_type"] == "payment"
    assert result["intent_type"] != "other"
