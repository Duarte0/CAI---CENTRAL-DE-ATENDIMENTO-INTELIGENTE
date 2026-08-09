import json
import asyncio

import pytest
from fastapi import Response

from src.api import routes
from src.core.models import MessageBuffer
from src.workers.ia_worker import IAWorker


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.queues = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def exists(self, key):
        return int(key in self.values)

    async def rpush(self, key, value):
        self.queues.setdefault(key, []).append(value)

    async def delete(self, key):
        self.values.pop(key, None)

    async def lpop(self, key):
        queue = self.queues.get(key, [])
        return queue.pop(0) if queue else None

    async def lrange(self, key, _start, _end):
        return list(self.queues.get(key, []))

    async def lpush(self, key, value):
        self.queues.setdefault(key, []).insert(0, value)

    async def lrem(self, key, _count, value):
        queue = self.queues.get(key, [])
        if value in queue:
            queue.remove(value)

    async def eval(self, script, keys, *args):
        if script == routes.SCHEDULE_TICKET_CLOSURE:
            token_key, due_key, queue_key, token, due_at, _ttl, conversation_id = *args,
            previous = self.values.get(token_key, "")
            self.values[token_key] = token
            self.values[due_key] = due_at
            self.queues.setdefault(queue_key, []).append(json.dumps({"conversation_id": conversation_id, "task_token": token}))
            return previous
        if script == routes.SCHEDULE_INITIAL_TICKET_CLOSURE:
            closure_key, token_key, due_key, buffer_key, queue_key, token, due_at, _ttl, conversation_id = *args,
            if closure_key in self.values:
                return ["duplicate", ""]
            self.values[closure_key] = "1"
            self.values[token_key] = token
            self.values[due_key] = due_at
            self.queues.setdefault(queue_key, []).append(json.dumps({"conversation_id": conversation_id, "task_token": token}))
            if buffer_key not in self.values:
                return ["scheduled_empty", token]
            return ["scheduled", token]
        if script == IAWorker.__module__:
            raise AssertionError("unreachable")
        if "return {'stale'" in script:
            token_key, due_key, buffer_key, last_message_key, claim_key, token, now, _ttl = *args,
            if claim_key in self.values:
                return ["claimed", self.values[claim_key], token]
            current = self.values.get(token_key)
            if current != token:
                return ["stale", current or ""]
            due_at = float(self.values.get(due_key, 0))
            if due_at > float(now):
                return ["not_due", str(due_at)]
            raw = self.values.get(buffer_key)
            if raw is None:
                return ["empty", self.values.get(last_message_key, ""), current]
            self.values[claim_key] = self.values.pop(buffer_key)
            self.values.pop(token_key, None)
            self.values.pop(due_key, None)
            return ["claimed", raw, current]

        key, last_message_key, closure_key, raw_message, now, conversation_id, _ttl = *args,
        raw = self.values.get(key)
        buffer = json.loads(raw) if raw else {
            "conversation_id": conversation_id, "messages": [], "last_activity": now,
            "is_active": True, "message_count": 0,
        }
        message = json.loads(raw_message)
        if not any(m.get("id") == message.get("id") for m in buffer["messages"]):
            buffer["messages"].append(message)
            buffer["messages"].sort(key=lambda m: m.get("timestamp", ""))
        buffer["message_count"] = len(buffer["messages"])
        buffer["last_activity"] = now
        self.values[key] = json.dumps(buffer)
        self.values[last_message_key] = now
        return [buffer["message_count"], int(closure_key in self.values)]


def message(message_id, text, is_from_me, timestamp, **extra):
    return {
        "event": "message.created",
        "data": {"id": message_id, "ticketId": "ticket-1", "text": text,
                 "isFromMe": is_from_me, "timestamp": timestamp,
                 "userId": "agent-1" if is_from_me else None, **extra},
    }


async def send(monkeypatch, redis, payload, update_protocol=None):
    async def fake_parse(_request):
        return payload, None
    if update_protocol is None:
        async def update_protocol(_conversation_id, _protocol):
            return False
    monkeypatch.setattr(routes, "parse_webhook_payload", fake_parse)
    monkeypatch.setattr(routes, "update_analysis_protocol", update_protocol)
    return await routes.digisac_webhook(request=None, response=Response(), redis=redis)


@pytest.mark.asyncio
async def test_buffers_interleaved_parties_in_timestamp_order_without_ia_before_closure(monkeypatch):
    redis = MemoryRedis()
    await send(monkeypatch, redis, message("m4", "Vou enviar agora", True, "2026-07-20T12:03:00Z"))
    await send(monkeypatch, redis, message("m2", "Como posso ajudar?", True, "2026-07-20T12:01:00Z"))
    await send(monkeypatch, redis, message("m1", "Preciso de uma guia", False, "2026-07-20T09:00:00-03:00"))
    await send(monkeypatch, redis, message("m3", "É a guia do FGTS", False, "2026-07-20T12:02:00Z"))

    buffer = MessageBuffer.model_validate_json(redis.values["buffer:ticket-1"])
    assert buffer.get_consolidated_context() == ("Cliente: Preciso de uma guia\nAtendente: Como posso ajudar?\n"
                                                 "Cliente: É a guia do FGTS\nAtendente: Vou enviar agora")
    assert "ia_queue" not in redis.queues


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bot_fields",
    [
        {"isFromBot": True, "origin": "bot"},
        {"origin": "bot"},
    ],
)
async def test_bot_message_does_not_touch_buffer_or_scheduling_state(monkeypatch, caplog, bot_fields):
    redis = MemoryRedis()
    redis.values["ticket_close_scheduled:ticket-1"] = "1"
    redis.values["ticket_close_task:ticket-1"] = "existing-token"
    redis.values["ticket_classify_after:ticket-1"] = "existing-due-at"

    with caplog.at_level("INFO"):
        result = await send(
            monkeypatch,
            redis,
            message("bot-1", "Mensagem automática", True, "2026-07-20T12:00:00Z", **bot_fields),
        )

    assert result["status"] == "ignored"
    assert result["reason"] in {"is_from_bot", "bot_origin_fallback"}
    assert "buffer:ticket-1" not in redis.values
    assert "ticket_last_message_at:ticket-1" not in redis.values
    assert redis.values["ticket_close_task:ticket-1"] == "existing-token"
    assert redis.values["ticket_classify_after:ticket-1"] == "existing-due-at"
    assert "ia_queue" not in redis.queues
    record = next(record for record in caplog.records if record.message == "Bot message ignored")
    assert record.message_id == "bot-1"
    assert record.ticket_id == "ticket-1"
    assert record.reason == result["reason"]


@pytest.mark.asyncio
async def test_bot_is_excluded_from_human_context_ids_and_count(monkeypatch):
    redis = MemoryRedis()
    await send(monkeypatch, redis, message("human-agent", "Boa tarde", True, "2026-07-20T12:00:00Z", isFromBot=False, origin="user"))
    await send(monkeypatch, redis, message("bot", "Mensagem automática", True, "2026-07-20T12:01:00Z", isFromBot=True, origin="bot"))
    await send(monkeypatch, redis, message("customer", "Qual é a senha?", False, "2026-07-20T12:02:00Z", isFromBot=False, origin="whatsapp"))

    buffer = MessageBuffer.model_validate_json(redis.values["buffer:ticket-1"])
    assert buffer.message_count == 2
    assert buffer.get_message_ids() == ["human-agent", "customer"]
    assert buffer.get_consolidated_context() == "Atendente: Boa tarde\nCliente: Qual é a senha?"
    assert "Mensagem automática" not in buffer.get_consolidated_context()


@pytest.mark.asyncio
async def test_bot_only_conversation_leaves_no_classifiable_buffer(monkeypatch):
    redis = MemoryRedis()
    await send(
        monkeypatch,
        redis,
        message("bot", "Mensagem automática", True, "2026-07-20T12:00:00Z", isFromBot=True, origin="bot"),
    )

    assert "buffer:ticket-1" not in redis.values
    assert "ticket_last_message_at:ticket-1" not in redis.values
    assert "ia_queue" not in redis.queues


@pytest.mark.asyncio
async def test_closure_queues_once_and_late_messages_join_the_same_buffer(monkeypatch):
    redis = MemoryRedis()
    await send(monkeypatch, redis, message("m1", "Preciso de ajuda", False, "2026-07-20T12:00:00Z"))
    closing = {"event": "ticket.updated", "data": {"id": "ticket-1", "isOpen": False, "protocol": "2026072223796"}}

    assert (await send(monkeypatch, redis, closing))["queued"] is True
    assert (await send(monkeypatch, redis, closing))["status"] == "ticket_already_closed"
    late = await send(monkeypatch, redis, message("m2", "Mensagem tardia", True, "2026-07-20T12:02:00Z"))

    assert len(redis.queues["ia_queue"]) == 2
    first, replacement = map(json.loads, redis.queues["ia_queue"])
    assert first["task_token"] != replacement["task_token"]
    assert redis.values["ticket_close_task:ticket-1"] == replacement["task_token"]
    assert late["status"] == "received"
    assert MessageBuffer.model_validate_json(redis.values["buffer:ticket-1"]).message_count == 2


@pytest.mark.asyncio
async def test_empty_ticket_closure_is_scheduled_to_catch_a_racing_message(monkeypatch):
    redis = MemoryRedis()
    response = await send(monkeypatch, redis, {"event": "ticket.updated", "data": {"id": "empty", "isOpen": False, "protocol": "123"}})
    assert response == {"status": "ticket_closed", "conversation_id": "empty", "queued": True}
    assert len(redis.queues["ia_queue"]) == 1


@pytest.mark.asyncio
async def test_closed_ticket_stores_protocol_and_updates_existing_analysis(monkeypatch):
    redis = MemoryRedis()
    redis.values["ia_result:ticket-1"] = json.dumps({
        "title": "Emissão de NF", "intent_type": "request"
    })
    updates = []

    async def update(conversation_id, protocol):
        updates.append((conversation_id, protocol))
        return True

    payload = {
        "event": "ticket.updated",
        "data": {"id": "ticket-1", "isOpen": False, "protocol": 2026072223796},
    }
    response = await send(monkeypatch, redis, payload, update)

    assert response["status"] == "ticket_closed"
    assert redis.values["ticket_protocol:ticket-1"] == "2026072223796"
    assert updates == [("ticket-1", "2026072223796")]
    cached = json.loads(redis.values["ia_result:ticket-1"])
    assert cached["title"] == "Emissão de NF"
    assert cached["display_title"] == "[2026072223796] - Emissão de NF"


@pytest.mark.asyncio
async def test_duplicate_closure_keeps_one_protocol_and_one_initial_job(monkeypatch):
    redis = MemoryRedis()
    payload = {
        "event": "ticket.updated",
        "data": {"id": "ticket-1", "isOpen": False, "protocol": "123"},
    }

    await send(monkeypatch, redis, payload)
    duplicate = await send(monkeypatch, redis, payload)

    assert duplicate["status"] == "ticket_already_closed"
    assert redis.values["ticket_protocol:ticket-1"] == "123"
    assert len(redis.queues["ia_queue"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"event": "ticket.updated", "data": {"id": "ticket-1", "isOpen": False}}, "missing_protocol"),
        ({"event": "ticket.updated", "data": {"id": "ticket-1", "isOpen": False, "protocol": "  "}}, "missing_protocol"),
        ({"event": "ticket.updated", "data": {"isOpen": False, "protocol": "123"}}, "missing_ticket_id"),
    ],
)
async def test_invalid_closed_ticket_is_ignored(monkeypatch, payload, reason):
    redis = MemoryRedis()
    response = await send(monkeypatch, redis, payload)
    assert response["status"] == "ignored"
    assert response["reason"] == reason
    assert "ia_queue" not in redis.queues


@pytest.mark.asyncio
async def test_open_ticket_does_not_store_protocol(monkeypatch):
    redis = MemoryRedis()
    response = await send(monkeypatch, redis, {
        "event": "ticket.updated",
        "data": {"id": "ticket-1", "isOpen": True, "protocol": "123"},
    })
    assert response["queued"] is False
    assert "ticket_protocol:ticket-1" not in redis.values


@pytest.mark.asyncio
async def test_database_error_does_not_abort_closed_ticket_webhook(monkeypatch):
    redis = MemoryRedis()

    async def fail_update(_conversation_id, _protocol):
        raise RuntimeError("database unavailable")

    response = await send(monkeypatch, redis, {
        "event": "ticket.updated",
        "data": {"id": "ticket-1", "isOpen": False, "protocol": "123"},
    }, fail_update)
    assert response["status"] == "ticket_closed"
    assert redis.values["ticket_protocol:ticket-1"] == "123"


@pytest.mark.asyncio
async def test_protocol_redis_error_does_not_abort_closure_scheduling(monkeypatch):
    class ProtocolFailingRedis(MemoryRedis):
        async def set(self, key, value, nx=False, ex=None):
            if key.startswith("ticket_protocol:"):
                raise RuntimeError("redis protocol write unavailable")
            return await super().set(key, value, nx=nx, ex=ex)

    redis = ProtocolFailingRedis()
    response = await send(monkeypatch, redis, {
        "event": "ticket.updated",
        "data": {"id": "ticket-1", "isOpen": False, "protocol": "123"},
    })
    assert response["status"] == "ticket_closed"


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_worker_inserts_once_and_clears_closed_ticket_buffer(monkeypatch):
    redis = MemoryRedis()
    redis.values["ticket_protocol:ticket-1"] = "2026072223796"
    redis.values["buffer:ticket-1"] = MessageBuffer(
        conversation_id="ticket-1", messages=[
            {"message_id": "m1", "content": "Preciso de uma guia", "is_from_me": False,
             "timestamp": "2026-07-20T12:00:00Z"},
            {"message_id": "audio-completed", "message_type": "ptt", "is_from_me": False,
             "timestamp": "2026-07-20T12:01:00Z"},
            {"message_id": "m2", "content": "Vou emitir", "is_from_me": True,
             "timestamp": "2026-07-20T12:02:00Z"},
            {"message_id": "audio-pending", "message_type": "ptt", "is_from_me": True,
             "timestamp": "2026-07-20T12:03:00Z"},
        ], message_count=4,
    ).model_dump_json()
    redis.values["ticket_close_task:ticket-1"] = "current"
    redis.values["ticket_classify_after:ticket-1"] = "0"
    redis.queues["ia_queue"] = [json.dumps({"conversation_id": "ticket-1", "task_token": "current"})]
    worker = IAWorker.__new__(IAWorker)
    worker.redis, worker.queue, worker.processing_queue, worker.dead_letter = redis, "ia_queue", "ia_processing", "ia_dead_letter"
    worker.max_retries, worker.result_ttl_seconds, worker.model, worker.prompt_version = 1, 60, "test", "v2"
    inserted = []

    async def no_duplicate(_ticket_id):
        return False

    async def insert(**kwargs):
        inserted.append(kwargs)
        return 1

    async def completed_transcriptions(message_ids):
        assert message_ids == ["audio-completed", "audio-pending"]
        return {"audio-completed": "É sobre pedido de demissão e aviso prévio."}

    async def analyze(_context, count):
        return {
            "intent_type": "request",
            "confidence": 0.9,
            "title": "Guia",
            "description": "Emitir guia",
            "department": ["inventado pela IA"],
            "agent": ["inventado pela IA"],
            "message_count": count,
            "processed_at": "2026-07-20T12:00:00+00:00",
        }

    async def assignments(_conversation_id):
        return ["Atendimento", "T.I."], ["Ana", "Guilherme"]

    monkeypatch.setattr("src.workers.ia_worker.ticket_has_classification", no_duplicate)
    monkeypatch.setattr("src.workers.ia_worker.insert_classification", insert)
    monkeypatch.setattr(
        "src.workers.ia_worker.get_completed_transcriptions",
        completed_transcriptions,
    )
    worker._analyze_with_groq = analyze
    worker._resolve_assignment_names = assignments
    task = asyncio.create_task(worker.process())
    for _ in range(20):
        if "buffer:ticket-1" not in redis.values:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "buffer:ticket-1" not in redis.values
    assert len(inserted) == 1
    assert inserted[0]["full_context"] == (
        "Cliente: Preciso de uma guia\n"
        "Cliente: [áudio transcrito] É sobre pedido de demissão e aviso prévio.\n"
        "Atendente: Vou emitir\n"
        "Atendente: enviou um áudio."
    )
    assert inserted[0]["protocol"] == "2026072223796"
    assert inserted[0]["result"]["department"] == ["Atendimento", "T.I."]
    assert inserted[0]["result"]["agent"] == ["Ana", "Guilherme"]
    cached_result = json.loads(redis.values["ia_result:ticket-1"])
    assert cached_result["title"] == "Guia"
    assert cached_result["protocol"] == "2026072223796"
    assert cached_result["display_title"] == "[2026072223796] - Guia"
    assert cached_result["department"] == ["Atendimento", "T.I."]
    assert cached_result["agent"] == ["Ana", "Guilherme"]


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_worker_excludes_bot_from_legacy_buffer_before_ai_and_persistence(monkeypatch):
    redis = MemoryRedis()
    redis.values["buffer:ticket-1"] = MessageBuffer(
        conversation_id="ticket-1",
        messages=[
            {"message_id": "customer-1", "content": "Oii", "is_from_me": False,
             "timestamp": "2026-07-20T12:00:00Z"},
            {"message_id": "bot-message-id", "content": "Seja bem-vindo", "is_from_me": True,
             "is_from_bot": True, "origin": "bot", "timestamp": "2026-07-20T12:01:00Z"},
            {"message_id": "agent-1", "content": "oiii", "is_from_me": True,
             "timestamp": "2026-07-20T12:02:00Z"},
            {"message_id": "customer-2", "content": "Só teste", "is_from_me": False,
             "timestamp": "2026-07-20T12:03:00Z"},
        ],
        message_count=4,
    ).model_dump_json()
    redis.values["ticket_close_task:ticket-1"] = "current"
    redis.values["ticket_classify_after:ticket-1"] = "0"
    redis.queues["ia_queue"] = [json.dumps({"conversation_id": "ticket-1", "task_token": "current"})]
    worker = IAWorker.__new__(IAWorker)
    worker.redis, worker.queue, worker.processing_queue, worker.dead_letter = redis, "ia_queue", "ia_processing", "ia_dead_letter"
    worker.max_retries, worker.result_ttl_seconds, worker.model, worker.prompt_version = 1, 60, "test", "v2"
    inserted = []

    async def no_duplicate(_ticket_id): return False
    async def insert(**kwargs): inserted.append(kwargs); return 1
    async def analyze(_context, count):
        return {"intent_type": "request", "confidence": 0.9, "message_count": count,
                "processed_at": "2026-07-20T12:00:00+00:00"}

    monkeypatch.setattr("src.workers.ia_worker.ticket_has_classification", no_duplicate)
    monkeypatch.setattr("src.workers.ia_worker.insert_classification", insert)
    worker._analyze_with_groq = analyze
    task = asyncio.create_task(worker.process())
    for _ in range(20):
        if inserted: break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task

    assert inserted[0]["message_count"] == 3
    assert inserted[0]["message_ids"] == ["customer-1", "agent-1", "customer-2"]
    assert inserted[0]["full_context"] == "Cliente: Oii\nAtendente: oiii\nCliente: Só teste"
    assert "bot-message-id" not in inserted[0]["message_ids"]
    assert "Seja bem-vindo" not in inserted[0]["full_context"]


@pytest.mark.asyncio
async def test_reset_debounce_invalidates_old_task_and_claims_updated_buffer_atomically(monkeypatch):
    redis = MemoryRedis()
    await send(monkeypatch, redis, message("m1", "Primeira", False, "2026-07-20T12:00:00Z"))
    closing = {"event": "ticket.updated", "data": {"id": "ticket-1", "isOpen": False, "protocol": "123"}}
    await send(monkeypatch, redis, closing)
    old_job = json.loads(redis.queues["ia_queue"][0])
    await send(monkeypatch, redis, message("m2", "Segunda", True, "2026-07-20T12:01:00Z"))
    new_job = json.loads(redis.queues["ia_queue"][1])
    redis.values["ticket_classify_after:ticket-1"] = "0"

    old_claim = await redis.eval(
        __import__("src.workers.ia_worker", fromlist=["CLAIM_TICKET_BUFFER"]).CLAIM_TICKET_BUFFER,
        5, "ticket_close_task:ticket-1", "ticket_classify_after:ticket-1",
        "buffer:ticket-1", "ticket_last_message_at:ticket-1",
        f"ticket_buffer_claim:ticket-1:{old_job['task_token']}", old_job["task_token"], "1", 60,
    )
    assert old_claim[0] == "stale"
    assert "buffer:ticket-1" in redis.values

    new_claim = await redis.eval(
        __import__("src.workers.ia_worker", fromlist=["CLAIM_TICKET_BUFFER"]).CLAIM_TICKET_BUFFER,
        5, "ticket_close_task:ticket-1", "ticket_classify_after:ticket-1",
        "buffer:ticket-1", "ticket_last_message_at:ticket-1",
        f"ticket_buffer_claim:ticket-1:{new_job['task_token']}", new_job["task_token"], "1", 60,
    )
    claimed = MessageBuffer.model_validate_json(new_claim[1])
    assert new_claim[0] == "claimed"
    assert claimed.message_count == 2
    assert "buffer:ticket-1" not in redis.values

    retry_claim = await redis.eval(
        __import__("src.workers.ia_worker", fromlist=["CLAIM_TICKET_BUFFER"]).CLAIM_TICKET_BUFFER,
        5, "ticket_close_task:ticket-1", "ticket_classify_after:ticket-1",
        "buffer:ticket-1", "ticket_last_message_at:ticket-1",
        f"ticket_buffer_claim:ticket-1:{new_job['task_token']}", new_job["task_token"], "2", 60,
    )
    assert retry_claim == new_claim



@pytest.mark.asyncio
@pytest.mark.postgres
async def test_mixed_text_media_and_bot_context(monkeypatch):
    redis = MemoryRedis()
    payloads = [
        message("text-customer", "Bom dia", False, "2026-07-20T12:00:00Z", type="chat"),
        message("document-customer", None, False, "2026-07-20T12:01:00Z", type="document", file={"name": "Notas fiscais.pdf", "url": "https://secret.invalid/signed"}),
        message("bot-id", None, True, "2026-07-20T12:02:00Z", type="document", isFromBot=True, origin="bot", file={"name": "Automático.pdf", "url": "https://secret.invalid/bot"}),
        message("text-agent", "Vou verificar", True, "2026-07-20T12:03:00Z", type="chat", isFromBot=False),
        message("audio-agent", None, True, "2026-07-20T12:04:00Z", type="ptt", isFromBot=False),
        message("image-customer", None, False, "2026-07-20T12:05:00Z", type="image", isFromBot=False),
    ]
    for payload in payloads:
        await send(monkeypatch, redis, payload)
    raw_buffer = redis.values["buffer:ticket-1"]
    buffer = MessageBuffer.model_validate_json(raw_buffer)
    assert buffer.message_count == 5
    assert buffer.get_message_ids() == ["text-customer", "document-customer", "text-agent", "audio-agent", "image-customer"]
    assert buffer.get_consolidated_context() == ("Cliente: Bom dia\n" 'Cliente: enviou um documento chamado "Notas fiscais.pdf".\n' "Atendente: Vou verificar\n" "Atendente: enviou um áudio.\n" "Cliente: enviou uma imagem.")
    assert "secret.invalid" not in raw_buffer
    assert "bot-id" not in raw_buffer


@pytest.mark.asyncio
async def test_bot_media_does_not_update_existing_debounce(monkeypatch):
    redis = MemoryRedis()
    redis.values["ticket_close_scheduled:ticket-1"] = "1"
    redis.values["ticket_close_task:ticket-1"] = "existing-token"
    redis.values["ticket_classify_after:ticket-1"] = "existing-due"
    result = await send(monkeypatch, redis, message("bot-media", None, True, "2026-07-20T12:00:00Z", type="document", isFromBot=True, origin="bot", file={"name": "Mensagem automática.pdf"}))
    assert result["status"] == "ignored"
    assert "buffer:ticket-1" not in redis.values
    assert "ticket_last_message_at:ticket-1" not in redis.values
    assert redis.values["ticket_close_task:ticket-1"] == "existing-token"
    assert redis.values["ticket_classify_after:ticket-1"] == "existing-due"
