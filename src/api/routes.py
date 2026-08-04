"""FastAPI application and HTTP endpoints."""

import asyncio
import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Mapping, cast

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel

from src.api.middleware import verify_webhook_signature
from src.api.webhook_adapter import DigisacMessage, DigisacWebhookAdapter
from src.api.webhook_adapter import AUDIO_MESSAGE_TYPES
from src.core.config import settings
from src.core.analysis import normalize_protocol, with_protocol
from src.core.db import (
    close_cycle,
    close_database,
    create_open_cycle,
    database_is_ready,
    get_cycle,
    get_cycle_metrics,
    get_cycle_result,
    get_latest_cycle,
    initialize_database,
    list_cycles,
    record_ticket_assignment,
    release_image_publication,
    release_transcription_publication,
    reserve_transcription,
    reserve_image_extraction,
    transition_cycle,
    update_analysis_protocol,
)
from src.core.digisac_directory import directory_sync_loop
from src.core.message_filter import is_bot_message
from src.core.media import is_image_message
from src.core.models import ConversationProcessing, WebhookPayload
from src.core.redis_client import AsyncRedis, create_redis_client
from src.utils.idempotency import IdempotencyService


logger = logging.getLogger(__name__)


def _dump(model: BaseModel) -> str:
    return model.model_dump_json()


def _non_empty_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _ticket_event_timestamp(
    payload: Mapping[str, Any], data: Mapping[str, Any]
) -> tuple[str, bool]:
    for value in (
        data.get("timestamp"),
        data.get("updatedAt"),
        payload.get("timestamp"),
        payload.get("createdAt"),
    ):
        parsed: datetime | None = None
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                parsed = datetime.fromtimestamp(value, timezone.utc)
            except (OverflowError, OSError, ValueError):
                continue
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat(), True
    return datetime.now(timezone.utc).isoformat(), False


def _ticket_transfer_count(data: Mapping[str, Any]) -> int | None:
    value = data.get("ticketTransferCount")
    metrics = data.get("metrics")
    if value is None and isinstance(metrics, Mapping):
        metrics_data = cast(Mapping[str, Any], metrics)
        value = metrics_data.get("ticketTransferCount")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _cycle_event_key(
    event: str,
    ticket_id: str,
    payload: Mapping[str, Any],
    data: Mapping[str, Any],
) -> str:
    source_event_id = _non_empty_string(payload.get("eventId") or payload.get("id"))
    if source_event_id:
        identity = f"{event}:{ticket_id}:{source_event_id}"
    else:
        timestamp, has_source_timestamp = _ticket_event_timestamp(payload, data)
        identity = json.dumps(
            [
                event,
                ticket_id,
                data.get("isOpen"),
                normalize_protocol(data.get("protocol")),
                timestamp if has_source_timestamp else None,
                data.get("lastMessageId"),
                data.get("ticketTransferCount"),
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


async def _publish_cycle(
    redis: AsyncRedis, cycle: Mapping[str, Any], *, attempt: int = 0
) -> None:
    public_id = cycle.get("public_id")
    conversation_id = cycle.get("conversation_id")
    if not public_id or not conversation_id:
        raise ValueError("cycle is missing its persistent identity")
    status_value = str(cycle.get("status") or "pending")
    marked = await transition_cycle(
        str(public_id),
        status_value,
        expected_statuses=(status_value,),
        fields={"enqueued_at": datetime.now(timezone.utc)},
    )
    if marked is None:
        return
    try:
        await redis.rpush(
            "ia_queue",
            json.dumps(
                {
                    "cycle_id": str(public_id),
                    "conversation_id": str(conversation_id),
                    "protocol": cycle.get("protocol"),
                    "attempt": attempt,
                    "not_before": (
                        datetime.fromisoformat(
                            str(cycle["next_attempt_at"])
                        ).timestamp()
                        if cycle.get("next_attempt_at")
                        else 0
                    ),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:
        await transition_cycle(
            str(public_id),
            status_value,
            expected_statuses=(status_value,),
            fields={"enqueued_at": None},
        )
        raise


async def capture_ticket_assignment(
    payload: Mapping[str, Any], data: Mapping[str, Any], ticket_id: str
) -> bool:
    """Persist assignment source data without delaying the webhook on failure."""
    department_id = _non_empty_string(data.get("departmentId"))
    user_id = _non_empty_string(data.get("userId"))
    if department_id is None and user_id is None:
        return False
    timestamp, has_source_timestamp = _ticket_event_timestamp(payload, data)
    source_event_id = _non_empty_string(
        payload.get("eventId") or payload.get("id")
    )
    if source_event_id:
        identity = f"ticket.updated:{ticket_id}:{source_event_id}"
    elif has_source_timestamp:
        identity = json.dumps(
            [ticket_id, department_id, user_id, timestamp],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    else:
        identity = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    event_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    try:
        inserted = await record_ticket_assignment(
            conversation_id=ticket_id,
            department_id=department_id,
            user_id=user_id,
            event_timestamp=timestamp,
            source_event_id=source_event_id,
            event_key=event_key,
            ticket_transfer_count=_ticket_transfer_count(data),
        )
    except Exception:
        logger.exception(
            "Failed to persist ticket assignment: conversation_id=%s "
            "department_id=%s user_id=%s",
            ticket_id,
            department_id,
            user_id,
        )
        return False
    logger.info(
        "Ticket assignment processed: conversation_id=%s inserted=%s "
        "department_id=%s user_id=%s transfer_count=%s",
        ticket_id,
        inserted,
        department_id,
        user_id,
        _ticket_transfer_count(data),
    )
    return inserted


APPEND_MESSAGE_TO_BUFFER = """
local key = KEYS[1]
local message = cjson.decode(ARGV[1])
local now = ARGV[2]
local conversation_id = ARGV[3]
local ttl = tonumber(ARGV[4])
local current = redis.call('GET', key)
local buffer
if current then
    buffer = cjson.decode(current)
else
    buffer = {conversation_id = conversation_id, messages = {}, last_activity = now,
              is_active = true, message_count = 0}
end
local duplicate = false
if message.id and message.id ~= cjson.null then
    for _, current_message in ipairs(buffer.messages) do
        if current_message.id == message.id then duplicate = true break end
    end
end
if not duplicate then
    table.insert(buffer.messages, message)
    table.sort(buffer.messages, function(a, b)
        local a_time = tonumber(a.timestamp_epoch) or 0
        local b_time = tonumber(b.timestamp_epoch) or 0
        if a_time == b_time then
            return tostring(a.id or "") < tostring(b.id or "")
        end
        return a_time < b_time
    end)
    buffer.message_count = #buffer.messages
end
buffer.last_activity = now
redis.call('SET', key, cjson.encode(buffer), 'EX', ttl)
redis.call('SET', KEYS[2], now, 'EX', ttl)
return {buffer.message_count, redis.call('EXISTS', KEYS[3])}
"""

SCHEDULE_TICKET_CLOSURE = """
local token_key = KEYS[1]
local due_key = KEYS[2]
local token = ARGV[1]
local due_at = ARGV[2]
local ttl = tonumber(ARGV[3])
local previous = redis.call('GET', token_key)
redis.call('SET', token_key, token, 'EX', ttl)
redis.call('SET', due_key, due_at, 'EX', ttl)
redis.call('RPUSH', KEYS[3], cjson.encode({conversation_id=ARGV[4], task_token=token}))
return previous or ''
"""

SCHEDULE_INITIAL_TICKET_CLOSURE = """
if redis.call('EXISTS', KEYS[1]) == 1 then return {'duplicate', ''} end
local ttl = tonumber(ARGV[3])
redis.call('SET', KEYS[1], '1', 'EX', ttl)
redis.call('SET', KEYS[2], ARGV[1], 'EX', ttl)
redis.call('SET', KEYS[3], ARGV[2], 'EX', ttl)
redis.call('RPUSH', KEYS[5], cjson.encode({conversation_id=ARGV[4], task_token=ARGV[1]}))
if redis.call('EXISTS', KEYS[4]) == 0 then return {'scheduled_empty', ARGV[1]} end
return {'scheduled', ARGV[1]}
"""


async def schedule_ticket_closure(
    redis: AsyncRedis, conversation_id: str
) -> tuple[str, str | None]:
    """Atomically replace the pending debounce generation for a ticket."""
    token = uuid.uuid4().hex
    due_at = (
        datetime.now(timezone.utc).timestamp()
        + settings.ticket_closure_debounce_seconds
    )
    previous = await redis.eval(
        SCHEDULE_TICKET_CLOSURE,
        3,
        f"ticket_close_task:{conversation_id}",
        f"ticket_classify_after:{conversation_id}",
        "ia_queue",
        token,
        str(due_at),
        settings.closed_ticket_ttl_seconds,
        conversation_id,
    )
    return token, previous or None


async def append_message_to_buffer(
    redis: AsyncRedis, payload: WebhookPayload | DigisacMessage
) -> tuple[int, bool]:
    """Append a message atomically, so concurrent webhooks do not lose data."""
    now = datetime.now(timezone.utc)
    message_timestamp = payload.get_timestamp() or now
    if message_timestamp.tzinfo is None:
        message_timestamp = message_timestamp.replace(tzinfo=timezone.utc)
    message_timestamp = message_timestamp.astimezone(timezone.utc)
    message = {
        "id": payload.get_message_id(),
        "message_id": payload.get_message_id(),
        "ticket_id": payload.get_conversation_id(),
        "text": payload.get_content(),
        "message_type": payload.get_message_type(),
        "file": payload.get_file(),
        "sender_id": payload.get_sender_id(),
        "isFromMe": getattr(payload, "is_from_me", False),
        "is_from_me": getattr(payload, "is_from_me", False),
        "is_from_bot": getattr(payload, "is_from_bot", None),
        "origin": getattr(payload, "origin", None),
        "userId": getattr(payload, "user_id", None),
        "event_type": payload.get_event(),
        "timestamp": message_timestamp.isoformat(),
        "timestamp_epoch": message_timestamp.timestamp(),
    }
    result = await redis.eval(
        APPEND_MESSAGE_TO_BUFFER,
        3,
        f"buffer:{payload.get_conversation_id()}",
        f"ticket_last_message_at:{payload.get_conversation_id()}",
        f"ticket_close_scheduled:{payload.get_conversation_id()}",
        json.dumps(message),
        now.isoformat(),
        payload.get_conversation_id(),
        settings.ticket_buffer_ttl_seconds,
    )
    return int(result[0]), bool(result[1])


async def enqueue_audio_transcription(
    redis: AsyncRedis, message: DigisacMessage
) -> bool:
    """Persist a durable reservation before publishing the lightweight job."""
    if message.message_type not in AUDIO_MESSAGE_TYPES or not message.message_id:
        return False
    reserved = await reserve_transcription(
        message.message_id,
        message.conversation_id,
        settings.audio_transcription_model,
    )
    if not reserved:
        return False
    try:
        await redis.rpush(
            "audio_transcription_queue",
            json.dumps(
                {
                    "message_id": message.message_id,
                    "conversation_id": message.conversation_id,
                    "attempt": 0,
                }
            ),
        )
    except Exception as exc:
        await release_transcription_publication(
            message.message_id, f"queue publish failed: {exc}"
        )
        raise
    return True


async def enqueue_image_extraction(
    redis: AsyncRedis, message: DigisacMessage
) -> bool:
    """Persist an idempotent reservation before publishing a vision job."""
    if not is_image_message(message.message_type, message.file) or not message.message_id:
        return False
    reserved = await reserve_image_extraction(
        message.message_id,
        message.conversation_id,
        settings.image_vision_model,
    )
    if not reserved:
        return False
    try:
        await redis.rpush(
            "image_extraction_queue",
            json.dumps(
                {
                    "message_id": message.message_id,
                    "conversation_id": message.conversation_id,
                    "attempt": 0,
                }
            ),
        )
    except Exception as exc:
        await release_image_publication(
            message.message_id, f"queue publish failed: {exc}"
        )
        raise
    return True


async def schedule_initial_ticket_closure(
    redis: AsyncRedis, conversation_id: str
) -> str:
    """Atomically check the buffer and create the ticket's first close task."""
    token = uuid.uuid4().hex
    due_at = (
        datetime.now(timezone.utc).timestamp()
        + settings.ticket_closure_debounce_seconds
    )
    result = await redis.eval(
        SCHEDULE_INITIAL_TICKET_CLOSURE,
        5,
        f"ticket_close_scheduled:{conversation_id}",
        f"ticket_close_task:{conversation_id}",
        f"ticket_classify_after:{conversation_id}",
        f"buffer:{conversation_id}",
        "ia_queue",
        token,
        str(due_at),
        settings.closed_ticket_ttl_seconds,
        conversation_id,
    )
    value = result[0]
    if not isinstance(value, str):
        raise RuntimeError("Redis returned an invalid ticket closure status")
    return value


async def associate_ticket_protocol(
    redis: AsyncRedis, conversation_id: str, protocol: str
) -> None:
    """Persist protocol now or leave Redis state for a racing IA insertion."""
    try:
        await redis.set(
            f"ticket_protocol:{conversation_id}",
            protocol,
            ex=settings.closed_ticket_ttl_seconds,
        )
    except Exception:
        logger.exception(
            "Failed to store ticket protocol state: conversation_id=%s",
            conversation_id,
        )

    try:
        analysis_exists = await update_analysis_protocol(conversation_id, protocol)
    except Exception:
        logger.exception(
            "Failed to associate protocol with analysis: conversation_id=%s",
            conversation_id,
        )
        return

    if analysis_exists:
        logger.info(
            "Protocol associated with analysis: conversation_id=%s", conversation_id
        )
    else:
        logger.info(
            "Analysis not found yet; protocol retained for later processing: "
            "conversation_id=%s",
            conversation_id,
        )

    try:
        raw_result = await redis.get(f"ia_result:{conversation_id}")
        if raw_result:
            parsed_result: Any = json.loads(raw_result)
            if not isinstance(parsed_result, Mapping):
                raise ValueError("Cached IA result must be a JSON object")
            await redis.set(
                f"ia_result:{conversation_id}",
                json.dumps(with_protocol(cast(dict[str, Any], parsed_result), protocol),
                           ensure_ascii=False),
                ex=settings.result_ttl_seconds,
            )
    except Exception:
        logger.exception(
            "Failed to refresh cached analysis protocol: conversation_id=%s",
            conversation_id,
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await initialize_database()
    app.state.redis = create_redis_client()
    directory_task = asyncio.create_task(directory_sync_loop())
    try:
        yield
    finally:
        directory_task.cancel()
        with suppress(asyncio.CancelledError):
            await directory_task
        await app.state.redis.aclose()
        await close_database()


app = FastAPI(title="Digisac Conversation Analyzer",
              version="1.0.0", lifespan=lifespan)


def get_redis(request: Request) -> AsyncRedis:
    return cast(AsyncRedis, request.app.state.redis)


@app.get("/health")
async def health(redis: AsyncRedis = Depends(get_redis)) -> dict[str, str]:
    await redis.ping()
    if not await database_is_ready():
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ok"}


@app.get("/queues")
async def queue_metrics(
    redis: AsyncRedis = Depends(get_redis),
) -> dict[str, Any]:
    """Small operational view of the Redis-backed work queues."""
    (
        ia_queue,
        processing_queue,
        dead_letter,
        audio_queue,
        audio_dead_letter,
        image_queue,
        image_dead_letter,
    ) = await asyncio.gather(
        redis.llen("ia_queue"),
        redis.llen("ia_processing"),
        redis.llen("ia_dead_letter"),
        redis.llen("audio_transcription_queue"),
        redis.llen("audio_transcription_dead_letter"),
        redis.llen("image_extraction_queue"),
        redis.llen("image_extraction_dead_letter"),
    )
    result: dict[str, Any] = {
        "ia_queue": ia_queue,
        "ia_processing": processing_queue,
        "ia_dead_letter": dead_letter,
        "audio_transcription_queue": audio_queue,
        "audio_transcription_dead_letter": audio_dead_letter,
        "image_extraction_queue": image_queue,
        "image_extraction_dead_letter": image_dead_letter,
    }
    result["conversation_cycles"] = await get_cycle_metrics()
    return result


async def parse_webhook_payload(
    request: Request,
) -> tuple[dict[str, Any], WebhookPayload]:
    """Read the raw JSON after HMAC validation and normalize its structure."""
    raw_body = await request.body()
    try:
        parsed_payload: Any = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.warning("Digisac webhook contains invalid JSON: %s", exc)
        raise HTTPException(
            status_code=400, detail="Invalid JSON payload") from exc

    if not isinstance(parsed_payload, dict):
        raise HTTPException(
            status_code=400, detail="Webhook payload must be an object"
        )
    raw_payload = cast(dict[str, Any], parsed_payload)
    logger.info(
        "Digisac webhook parsed: event=%r top_level_keys=%s",
        raw_payload.get("event"),
        sorted(raw_payload),
    )
    try:
        payload = WebhookPayload.model_validate(raw_payload)
    except ValueError as exc:
        logger.warning("Digisac webhook validation failed: %s", exc)
        raise HTTPException(
            status_code=400, detail="Webhook payload must be an object"
        ) from exc

    logger.info("Digisac webhook field extraction: %s",
                payload.extraction_debug())
    return raw_payload, payload


@app.post("/webhook/digisac", status_code=status.HTTP_202_ACCEPTED)
async def digisac_webhook(
    request: Request,
    response: Response,
    _: None = Depends(verify_webhook_signature),
    redis: AsyncRedis = Depends(get_redis),
) -> dict[str, Any]:
    """Buffer messages by ticket and schedule one IA job at closure."""
    payload_data, _payload = await parse_webhook_payload(request)
    data = payload_data.get("data")
    if not isinstance(data, dict):
        logger.warning(
            "Digisac webhook rejected: event=%r data_type=%s top_level_keys=%s",
            payload_data.get("event"),
            type(data).__name__,
            sorted(payload_data),
        )
        raise HTTPException(
            status_code=400,
            detail="Malformed Digisac payload: 'data' must be an object",
        )
    data = cast(dict[str, Any], data)

    event = payload_data.get("event")
    if event in {"ticket.created", "ticket.updated"}:
        ticket_id = data.get("id")
        if not isinstance(ticket_id, str) or not ticket_id:
            logger.warning(
                "Ticket webhook ignored: missing data.id event=%r", event)
            response.status_code = status.HTTP_200_OK
            return {"status": "ignored", "reason": "missing_ticket_id"}
        if event == "ticket.created":
            if settings.digisac_history_finalization_enabled:
                timestamp, _has_timestamp = _ticket_event_timestamp(
                    payload_data, data
                )
                cycle, created = await create_open_cycle(
                    conversation_id=ticket_id,
                    started_at=timestamp,
                    open_event_key=_cycle_event_key(
                        event, ticket_id, payload_data, data
                    ),
                    start_strategy="ticket_created_event",
                )
                return {
                    "status": "ticket_created",
                    "conversation_id": ticket_id,
                    "cycle_id": str(cycle["public_id"]),
                    "cycle_created": created,
                }
            return {"status": "ticket_created", "conversation_id": ticket_id}
        await capture_ticket_assignment(payload_data, data, ticket_id)
        if (
            settings.digisac_history_finalization_enabled
            and data.get("isOpen") is True
        ):
            timestamp, _has_timestamp = _ticket_event_timestamp(
                payload_data, data
            )
            cycle, created = await create_open_cycle(
                conversation_id=ticket_id,
                started_at=timestamp,
                open_event_key=_cycle_event_key(
                    event, ticket_id, payload_data, data
                ),
                start_strategy="ticket_reopened_event",
            )
            await redis.delete(
                f"ticket_close_scheduled:{ticket_id}",
                f"ticket_close_task:{ticket_id}",
                f"ticket_classify_after:{ticket_id}",
                f"ticket_last_message_at:{ticket_id}",
                f"buffer:{ticket_id}",
            )
            return {
                "status": "ticket_reopened",
                "conversation_id": ticket_id,
                "cycle_id": str(cycle["public_id"]),
                "cycle_created": created,
                "queued": False,
            }
        if data.get("isOpen") is not False:
            logger.info(
                "Ticket event ignored: ticket still open conversation_id=%s", ticket_id
            )
            return {
                "status": "ticket_updated",
                "conversation_id": ticket_id,
                "queued": False,
            }

        protocol = normalize_protocol(data.get("protocol"))
        if protocol is None:
            logger.warning(
                "Ticket closure ignored: missing protocol conversation_id=%s", ticket_id
            )
            response.status_code = status.HTTP_200_OK
            return {
                "status": "ignored",
                "reason": "missing_protocol",
                "conversation_id": ticket_id,
            }

        logger.info(
            "Closed ticket received: conversation_id=%s protocol=%s",
            ticket_id,
            protocol,
        )
        if settings.digisac_history_finalization_enabled:
            closed_at, _has_timestamp = _ticket_event_timestamp(
                payload_data, data
            )
            cycle, created = await close_cycle(
                conversation_id=ticket_id,
                protocol=protocol,
                closed_at=closed_at,
                close_event_key=_cycle_event_key(
                    event, ticket_id, payload_data, data
                ),
            )
            if created:
                try:
                    await _publish_cycle(redis, cycle)
                except Exception:
                    logger.exception(
                        "Cycle persisted but queue publication failed: "
                        "cycle_id=%s conversation_id=%s",
                        cycle["public_id"],
                        ticket_id,
                    )
            await redis.set(
                f"ia_status:{ticket_id}",
                json.dumps(
                    {
                        "conversation_id": ticket_id,
                        "cycle_id": str(cycle["public_id"]),
                        "status": cycle["status"],
                        "started_at": cycle["created_at"],
                        "completed_at": None,
                        "retry_count": cycle["attempt_count"],
                        "max_retries": settings.max_retry_attempts,
                    }
                ),
                ex=settings.result_ttl_seconds,
            )
            return {
                "status": (
                    "ticket_closed" if created else "ticket_already_closed"
                ),
                "conversation_id": ticket_id,
                "cycle_id": str(cycle["public_id"]),
                "queued": created,
            }
        await associate_ticket_protocol(redis, ticket_id, protocol)

        closure_status = await schedule_initial_ticket_closure(redis, ticket_id)
        if closure_status == "duplicate":
            return {
                "status": "ticket_already_closed",
                "conversation_id": ticket_id,
                "queued": False,
            }
        if closure_status == "scheduled_empty":
            last_message_at = await redis.get(f"ticket_last_message_at:{ticket_id}")
            logger.warning(
                "Ticket closure scheduled with empty buffer: ticket_id=%s "
                "last_message_at=%s closure_at=%s concurrent_task=%s",
                ticket_id,
                last_message_at,
                datetime.now(timezone.utc).isoformat(),
                False,
            )
            logger.info(
                "Empty closure remains scheduled to catch late messages: ticket_id=%s",
                ticket_id,
            )
        processing = ConversationProcessing(
            conversation_id=ticket_id,
            max_retries=settings.max_retry_attempts,
        )
        await redis.set(
            f"ia_status:{ticket_id}", _dump(processing), ex=settings.result_ttl_seconds
        )
        return {"status": "ticket_closed", "conversation_id": ticket_id, "queued": True}

    if event not in {"message.created", "message.updated"}:
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored", "reason": "unsupported_event"}

    logger.info(
        "Message bot detection input",
        extra={
            "message_id": data.get("id"),
            "ticket_id": data.get("ticketId"),
            "raw_is_from_bot": data.get("isFromBot"),
            "raw_origin": data.get("origin"),
            "raw_is_from_me": data.get("isFromMe"),
        },
    )
    if is_bot_message(is_from_bot=data.get("isFromBot"), origin=data.get("origin")):
        reason = (
            "is_from_bot" if data.get(
                "isFromBot") is True else "bot_origin_fallback"
        )
        logger.info(
            "Bot message ignored",
            extra={
                "reason": reason,
                "message_id": data.get("id"),
                "ticket_id": data.get("ticketId"),
                "is_from_bot": data.get("isFromBot"),
                "origin": data.get("origin"),
            },
        )
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored", "reason": reason}

    adaptation = DigisacWebhookAdapter.adapt(payload_data)
    if not adaptation.should_process:
        if adaptation.ignored_reason == "unsupported_message_type":
            logger.info(
                "Unsupported message type ignored",
                extra={
                    "message_id": data.get("id"),
                    "ticket_id": data.get("ticketId"),
                    "message_type": data.get("type"),
                },
            )
        logger.info(
            "Digisac webhook ignored: event=%r reason=%s data_keys=%s",
            payload_data.get("event"),
            adaptation.ignored_reason,
            sorted(data),
        )
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored", "reason": adaptation.ignored_reason}

    message = adaptation.message
    assert message is not None
    logger.info(
        "Message bot detection normalized",
        extra={
            "message_id": message.message_id,
            "ticket_id": message.conversation_id,
            "is_from_bot": message.is_from_bot,
            "origin": message.origin,
            "is_from_me": message.is_from_me,
        },
    )
    conversation_id = message.conversation_id
    idempotency_data = {
        **payload_data,
        "conversation_id": conversation_id,
        "message_id": message.message_id,
        "content": message.content,
        "event": message.event,
        "timestamp": (message.timestamp.isoformat() if message.timestamp else None),
    }
    idempotency = IdempotencyService(redis)
    event_id = idempotency.generate_event_id(idempotency_data)
    # Reserve/enqueue before event idempotency: if Redis publishing fails, the
    # failed DB row can be reserved again when DigiSac retries the same webhook.
    transcription_queued = await enqueue_audio_transcription(redis, message)
    image_extraction_queued = await enqueue_image_extraction(redis, message)
    if settings.digisac_history_finalization_enabled:
        return {
            "status": "received",
            "conversation_id": conversation_id,
            "transcription_queued": transcription_queued,
            "image_extraction_queued": image_extraction_queued,
        }
    if not await idempotency.try_mark_processed(event_id):
        return {"status": "duplicate", "conversation_id": conversation_id}

    _buffered_count, closed = await append_message_to_buffer(redis, message)
    if closed:
        # Digisac can deliver message.created after ticket.updated. Extend the
        # settling window so the worker cannot snapshot a partial history.
        token, replaced = await schedule_ticket_closure(redis, conversation_id)
        logger.info(
            "Reset ticket closure debounce: ticket_id=%s message_id=%s task_token=%s "
            "replaced_task_token=%s",
            conversation_id,
            message.message_id,
            token,
            replaced,
        )
    return {
        "status": "received",
        "conversation_id": conversation_id,
        "transcription_queued": transcription_queued,
        "image_extraction_queued": image_extraction_queued,
    }


@app.post("/webhook/debug")
async def debug_digisac_webhook(
    request: Request,
    _: None = Depends(verify_webhook_signature),
) -> dict[str, Any]:
    """Inspect a Digisac payload without writing to Redis or queuing work."""
    raw_payload, payload = await parse_webhook_payload(request)
    timestamp = payload.get_timestamp()
    adaptation = DigisacWebhookAdapter.adapt(raw_payload)
    return {
        "raw_payload": raw_payload,
        "extraction": payload.extraction_debug(),
        "normalized": {
            "conversation_id": payload.get_conversation_id(),
            "message_id": payload.get_message_id(),
            "content": payload.get_content(),
            "sender_id": payload.get_sender_id(),
            "event": payload.get_event(),
            "timestamp": timestamp.isoformat() if timestamp else None,
        },
        "digisac_adapter": {
            "should_process": adaptation.should_process,
            "ignored_reason": adaptation.ignored_reason,
            "message": (
                {
                    "conversation_id": adaptation.message.conversation_id,
                    "message_id": adaptation.message.message_id,
                    "content": adaptation.message.content,
                    "sender_id": adaptation.message.sender_id,
                }
                if adaptation.message
                else None
            ),
        },
    }


@app.get(
    "/conversations/{conversation_id}/status", response_model=ConversationProcessing
)
async def conversation_status(
    conversation_id: str, redis: AsyncRedis = Depends(get_redis)
) -> ConversationProcessing:
    if settings.digisac_history_finalization_enabled:
        cycle = await get_latest_cycle(conversation_id)
        if cycle:
            return ConversationProcessing(
                conversation_id=conversation_id,
                status=cycle["status"],
                started_at=cycle["created_at"],
                completed_at=cycle.get("completed_at"),
                error_message=cycle.get("error_message"),
                retry_count=cycle["attempt_count"],
                transient_retry_count=cycle.get("transient_retry_count", 0),
                max_retries=settings.max_retry_attempts,
            )
    data = await redis.get(f"ia_status:{conversation_id}")
    if not data:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationProcessing.model_validate_json(data)


@app.get("/conversations/{conversation_id}/result")
async def conversation_result(
    conversation_id: str, redis: AsyncRedis = Depends(get_redis)
) -> Any:
    if settings.digisac_history_finalization_enabled:
        cycle = await get_latest_cycle(conversation_id)
        if cycle:
            result = await get_cycle_result(str(cycle["public_id"]))
            if result and result.get("classification_public_id"):
                return result
    data = await redis.get(f"ia_result:{conversation_id}")
    if not data:
        raise HTTPException(status_code=404, detail="Result not available")
    return json.loads(data)


@app.get("/conversations/{conversation_id}/cycles")
async def conversation_cycles(
    conversation_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    return await list_cycles(conversation_id, limit=limit)


@app.get("/cycles/{cycle_id}/status")
async def cycle_status(cycle_id: str) -> dict[str, Any]:
    cycle = await get_cycle(cycle_id)
    if not cycle:
        raise HTTPException(status_code=404, detail="Cycle not found")
    return cycle


@app.get("/cycles/{cycle_id}/result")
async def cycle_result(cycle_id: str) -> dict[str, Any]:
    result = await get_cycle_result(cycle_id)
    if not result:
        raise HTTPException(status_code=404, detail="Cycle not found")
    if not result.get("classification_public_id"):
        raise HTTPException(status_code=404, detail="Cycle result not available")
    return result
