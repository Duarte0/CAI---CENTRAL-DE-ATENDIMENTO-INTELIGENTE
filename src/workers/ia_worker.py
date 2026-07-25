import os
import json
import re
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, cast
from groq import Groq

from src.core.config import settings
from src.core.analysis import normalize_protocol, with_protocol
from src.core.db import (
    close_database,
    get_completed_image_extractions,
    get_completed_transcriptions,
    get_pending_content_extractions,
    initialize_database,
    insert_classification,
    resolve_ticket_assignments,
    ticket_has_classification,
)
from src.core.digisac_directory import sync_digisac_directories
from src.core.models import MessageBuffer
from src.core.intents import normalize_intent_type
from src.core.redis_client import AsyncRedis, create_redis_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CLAIM_TICKET_BUFFER = """
local claimed = redis.call('GET', KEYS[5])
if claimed then
    return {'claimed', claimed, ARGV[1]}
end
local current_token = redis.call('GET', KEYS[1])
if not current_token or current_token ~= ARGV[1] then
    return {'stale', current_token or ''}
end
local due_at = tonumber(redis.call('GET', KEYS[2]) or '0')
if due_at > tonumber(ARGV[2]) then
    return {'not_due', tostring(due_at)}
end
local raw = redis.call('GET', KEYS[3])
if not raw then
    return {'empty', redis.call('GET', KEYS[4]) or '', current_token}
end
redis.call('RENAME', KEYS[3], KEYS[5])
redis.call('EXPIRE', KEYS[5], tonumber(ARGV[3]))
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[2])
return {'claimed', raw, current_token}
"""


class IAWorker:
    def __init__(self, redis_client: AsyncRedis):
        self.redis = redis_client
        self.queue = "ia_queue"
        self.processing_queue = "ia_processing"
        self.dead_letter = "ia_dead_letter"

        # Usa GROQ_API_KEY
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY is required to run the IA worker")

        # Inicializa cliente Groq
        self.client = Groq(api_key=api_key)
        self.model = os.getenv("MODEL_NAME", settings.model_name)
        configured_max_tokens = int(
            os.getenv("MAX_TOKENS", settings.max_tokens))
        # GPT-OSS contabiliza reasoning e resposta final no mesmo orçamento.
        # Com 500 tokens, respostas perfeitamente válidas eram cortadas no meio.
        self.max_tokens = max(configured_max_tokens, 1000)
        if self.max_tokens != configured_max_tokens:
            logger.warning(
                "MAX_TOKENS=%s is too low for GPT-OSS structured output; using %s",
                configured_max_tokens,
                self.max_tokens,
            )
        self.max_retries = int(os.getenv("MAX_RETRY_ATTEMPTS", 3))
        self.result_ttl_seconds = int(os.getenv("RESULT_TTL_SECONDS", 86400))
        self.prompt_version = settings.prompt_version

    async def process(self) -> None:
        """Processa mensagens da fila de IA"""
        logger.info("🤖 IA Worker started with Groq")

        # Recupera itens que estavam em processamento
        await self._recover_processing_items()

        while True:
            try:
                item = await self.redis.lpop(self.queue)
                if not item:
                    await asyncio.sleep(1)
                    continue

                parsed_data: Any = json.loads(item)
                if not isinstance(parsed_data, dict):
                    raise ValueError("IA queue item must be a JSON object")
                data = cast(dict[str, Any], parsed_data)
                conversation_id = data.get("conversation_id")
                logger.info(f"📥 Processando conversa: {conversation_id}")

                # Marca como em processamento
                await self.redis.rpush(self.processing_queue, item)

                try:
                    if not isinstance(conversation_id, str) or not conversation_id:
                        raise ValueError("IA job missing conversation_id")
                    task_token = data.get("task_token")
                    while True:
                        classify_after = await self.redis.get(
                            f"ticket_classify_after:{conversation_id}"
                        )
                        if not classify_after:
                            break
                        remaining = float(classify_after) - \
                            datetime.now(timezone.utc).timestamp()
                        if remaining <= 0:
                            break
                        await asyncio.sleep(remaining)
                    if not isinstance(task_token, str) or not task_token:
                        raise ValueError("IA job missing task_token")
                    raw_claim: Any = await self.redis.eval(
                        CLAIM_TICKET_BUFFER, 5,
                        f"ticket_close_task:{conversation_id}",
                        f"ticket_classify_after:{conversation_id}",
                        f"buffer:{conversation_id}",
                        f"ticket_last_message_at:{conversation_id}",
                        f"ticket_buffer_claim:{conversation_id}:{task_token}",
                        task_token, str(datetime.now(
                            timezone.utc).timestamp()),
                        settings.ticket_buffer_ttl_seconds,
                    )
                    if (
                        not isinstance(raw_claim, list)
                        or not raw_claim
                        or not all(
                            isinstance(value, str)
                            for value in cast(list[object], raw_claim)
                        )
                    ):
                        raise RuntimeError("Redis returned an invalid ticket buffer claim")
                    claim = cast(list[str], raw_claim)
                    claim_status = claim[0]
                    if claim_status == "stale":
                        logger.info(
                            "Skipping replaced closure task: ticket_id=%s task_token=%s "
                            "current_task_token=%s",
                            conversation_id, task_token, claim[1] or None,
                        )
                        continue
                    if claim_status == "not_due":
                        await self.redis.rpush(self.queue, item)
                        continue
                    if claim_status == "empty":
                        logger.warning(
                            "Ticket closure found empty buffer: ticket_id=%s "
                            "last_message_at=%s closure_at=%s task_token=%s concurrent_task=%s",
                            conversation_id, claim[1] or None, datetime.now(
                                timezone.utc).isoformat(),
                            task_token, bool(
                                claim[2] and claim[2] != task_token),
                        )
                        continue
                    if await ticket_has_classification(conversation_id):
                        await self.redis.delete(
                            f"ticket_buffer_claim:{conversation_id}:{task_token}"
                        )
                        logger.info(
                            "Classification already persisted for ticket %s", conversation_id)
                        continue
                    raw_buffer = claim[1]
                    buffer = MessageBuffer.model_validate_json(raw_buffer)
                    human_buffer = buffer.human_buffer()
                    ignored_bot_count = len(
                        buffer.messages) - len(human_buffer.messages)
                    if ignored_bot_count:
                        logger.info(
                            "Bot messages removed from claimed buffer: ticket_id=%s count=%s",
                            conversation_id,
                            ignored_bot_count,
                        )
                    message_ids = human_buffer.get_message_ids()
                    audio_message_ids = human_buffer.get_audio_message_ids()
                    image_message_ids = human_buffer.get_image_message_ids()
                    await self._wait_for_content_extractions(
                        audio_message_ids,
                        image_message_ids,
                    )
                    transcriptions, image_extractions = await asyncio.gather(
                        get_completed_transcriptions(audio_message_ids),
                        get_completed_image_extractions(image_message_ids),
                    )
                    context = human_buffer.get_consolidated_context(
                        transcriptions,
                        image_extractions,
                    )
                    message_count = human_buffer.message_count
                    if not context:
                        logger.warning(
                            "Ticket %s has no text to classify; skipping", conversation_id)
                        await self.redis.delete(
                            f"ticket_buffer_claim:{conversation_id}:{task_token}"
                        )
                        continue
                    logger.info("📝 Mensagens: %s", message_count)
                    # Analisa com IA
                    started_at = time.perf_counter()
                    result = await self._analyze_with_groq(context, message_count)
                    processing_time_ms = int(
                        (time.perf_counter() - started_at) * 1000)
                    department_list, agent_list = (
                        await self._resolve_assignment_names(conversation_id)
                    )
                    result = {
                        **result,
                        "department": department_list,
                        "agent": agent_list,
                    }
                    protocol = normalize_protocol(
                        await self.redis.get(f"ticket_protocol:{conversation_id}")
                    )

                    try:
                        await insert_classification(
                            conversation_id=conversation_id,
                            message_ids=message_ids,
                            created_at=result["processed_at"],
                            full_context=context,
                            message_count=message_count,
                            result=result,
                            model=self.model,
                            processing_time_ms=processing_time_ms,
                            prompt_version=self.prompt_version,
                            protocol=protocol,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to persist IA classification for %s; continuing",
                            conversation_id,
                        )
                        raise

                    # Salva resultado
                    result = with_protocol(result, protocol)
                    await self.redis.set(
                        f"ia_result:{conversation_id}",
                        json.dumps(result, ensure_ascii=False),
                        ex=self.result_ttl_seconds,
                    )
                    await self.redis.delete(
                        f"ticket_buffer_claim:{conversation_id}:{task_token}"
                    )

                    # Atualiza status (mantém conversation_id, exigido por ConversationProcessing)
                    await self.redis.set(
                        f"ia_status:{conversation_id}",
                        json.dumps({
                            "conversation_id": conversation_id,
                            "status": "completed",
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                        }),
                        ex=self.result_ttl_seconds,
                    )

                    logger.info(
                        f"✅ Conversa {conversation_id} processada com sucesso")
                except Exception as e:
                    logger.error(f"❌ Erro ao processar {conversation_id}: {e}")

                    # Incrementa tentativas
                    attempt = data.get("attempt", 0) + 1
                    data["attempt"] = attempt

                    if attempt >= self.max_retries:
                        # Vai para dead letter
                        await self.redis.rpush(self.dead_letter, json.dumps(data))
                        await self.redis.set(
                            f"ia_status:{conversation_id}",
                            json.dumps({
                                "conversation_id": conversation_id,
                                "status": "failed",
                                "error": str(e),
                            }),
                            ex=self.result_ttl_seconds,
                        )
                        logger.error(
                            f"💀 Máximo de tentativas para {conversation_id}")
                    else:
                        # Requeue
                        await self.redis.rpush(self.queue, json.dumps(data))
                        logger.info(
                            f"🔄 Reenfileirado {conversation_id} (tentativa {attempt})")

                finally:
                    # Remove do processamento
                    await self._remove_from_processing(item)

            except Exception as e:
                logger.error(f"⚠️ Erro no worker: {e}")
                await asyncio.sleep(1)

    async def _analyze_with_groq(self, context: str, message_count: int) -> Dict[str, Any]:
        """Analisa a conversa usando GROQ"""
        if not context.strip():
            # Sem conteúdo real para analisar — não vale a pena chamar o modelo.
            return {
                "intent_type": "other",
                "confidence": 0.0,
                "title": "Sem conteúdo para análise",
                "description": "O buffer da conversa chegou vazio ao worker de IA.",
                "message_count": message_count,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }

        prompt = self._build_prompt(context)

        try:
            logger.info(f"📤 Chamando Groq API com {message_count} mensagens")

            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
                # Supported by the Groq HTTP API even on older SDK clients.
                extra_body={"reasoning_effort": "low"},
            )

            choice = response.choices[0]
            result_text = choice.message.content
            if choice.finish_reason == "length":
                raise RuntimeError(
                    "Groq classification response exceeded the output token limit"
                )
            if not result_text or not result_text.strip():
                raise RuntimeError(
                    "Groq returned an empty classification response")

            result = self._parse_result(result_text)

            result["message_count"] = message_count
            result["processed_at"] = datetime.now(timezone.utc).isoformat()
            return result

        except Exception as e:
            logger.error(f"❌ Erro na API Groq: {e}")
            raise

    def _parse_result(self, result_text: str) -> Dict[str, Any]:
        """Extrai o JSON da resposta do modelo, tolerando markdown/texto solto."""
        try:
            return self._validate_result(json.loads(result_text))
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
                "json_offset=%s raw_response=%r",
                match.start(),
                result_text,
            )
            return self._validate_result(typed_candidate)

        logger.warning(
            "Groq response does not contain a complete classification JSON; "
            "response_preview=%r",
            result_text[:500],
        )
        # Never persist the raw/truncated model response as business data. Raising
        # lets the existing retry/dead-letter policy handle transient generations.
        raise ValueError(
            "Groq response does not contain valid classification JSON")

    def _validate_result(self, result: Any) -> Dict[str, Any]:
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

    def _system_prompt(self) -> str:
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

    def _build_prompt(self, context: str) -> str:
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
   • Atendente confirmou o envio do documento; 
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
  "intent_type": "um de: question, problem, request, complaint, payment, billing, document, protocol, other",
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
contesta o valor, sem indicar que vai pagar); "document" para envio, reenvio
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

    async def _recover_processing_items(self) -> None:
        """Recupera itens que estavam em processamento"""
        items = await self.redis.lrange(self.processing_queue, 0, -1)
        for item in items:
            await self.redis.lpush(self.queue, item)
            await self.redis.lrem(self.processing_queue, 0, item)
        if items:
            logger.info(
                f"♻️ Recuperados {len(items)} itens da fila de processamento")

    async def _wait_for_content_extractions(
        self,
        audio_message_ids: list[str],
        image_message_ids: list[str],
    ) -> None:
        """Wait once for both media pipelines, then preserve placeholders on timeout."""
        if not audio_message_ids and not image_message_ids:
            return
        deadline = time.monotonic() + settings.content_extraction_wait_seconds
        while True:
            pending = await get_pending_content_extractions(
                audio_message_ids,
                image_message_ids,
            )
            if not pending:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "Content extraction wait timed out; pending_message_ids=%s",
                    sorted(pending),
                )
                return
            await asyncio.sleep(
                min(settings.content_extraction_poll_seconds, remaining)
            )

    async def _resolve_assignment_names(
        self, conversation_id: str
    ) -> tuple[list[str], list[str]]:
        (
            departments,
            agents,
            unresolved_departments,
            unresolved_users,
        ) = await resolve_ticket_assignments(conversation_id)
        if unresolved_departments or unresolved_users:
            await sync_digisac_directories(force=False)
            (
                departments,
                agents,
                unresolved_departments,
                unresolved_users,
            ) = await resolve_ticket_assignments(conversation_id)
        if unresolved_departments or unresolved_users:
            logger.warning(
                "Ticket assignment names remain unresolved: "
                "conversation_id=%s department_ids=%s user_ids=%s",
                conversation_id,
                unresolved_departments,
                unresolved_users,
            )
        return departments, agents

    async def _remove_from_processing(self, item: str) -> None:
        """Remove item da fila de processamento"""
        await self.redis.lrem(self.processing_queue, 0, item)


async def main() -> None:
    """Função principal"""
    redis_client = None
    try:
        await initialize_database()
        redis_client = create_redis_client()
        await redis_client.ping()
        logger.info("✅ Conectado ao Redis")
        await IAWorker(redis_client).process()
    except Exception as e:
        logger.error(f"❌ Erro de inicialização/execução do worker: {e}")
    finally:
        if redis_client is not None:
            await redis_client.aclose()
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
