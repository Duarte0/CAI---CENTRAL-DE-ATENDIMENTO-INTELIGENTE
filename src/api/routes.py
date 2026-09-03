"""FastAPI application and HTTP endpoints."""

import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Mapping, cast

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.api.middleware import verify_webhook_signature
from src.api.admin_routes import admin_router
from src.api.admin_ui import admin_ui_router
from src.api.openapi import install_openapi_contract
from src.api.webhook_adapter import DigisacMessage, DigisacWebhookAdapter
from src.api.webhook_adapter import AUDIO_MESSAGE_TYPES, SUPPORTED_MESSAGE_TYPES
from src.core.config import require_admin_api_token, settings
from src.core.analysis import normalize_protocol
from src.core.db import (
    close_cycle,
    close_database,
    create_open_cycle,
    database_is_ready,
    get_cycle,
    get_cycle_metrics,
    get_cycle_work_metrics,
    get_image_extraction_work_metrics,
    get_transcription_work_metrics,
    get_cycle_result,
    get_latest_cycle,
    initialize_database,
    list_cycles,
    record_ticket_assignment,
    reserve_transcription,
    reserve_image_extraction,
    upsert_digisac_contact,
)
from src.core.digisac_contact_hydration import (
    contact_hydration_loop,
    request_contact_hydration,
)
from src.core.digisac_client import DigisacResponseError, normalize_contact
from src.core.digisac_directory import directory_sync_loop
from src.core.message_filter import is_bot_message
from src.core.media import is_image_message
from src.core.models import ConversationProcessing, WebhookPayload
from src.core.redis_client import AsyncRedis, create_redis_client
from src.utils.idempotency import IdempotencyService


logger = logging.getLogger(__name__)

_SUPPORTED_WEBHOOK_EVENTS = {
    "ticket.created",
    "ticket.updated",
    "message.created",
    "message.updated",
}


def _safe_webhook_event(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "missing"
    event = value.strip()
    return event if event in _SUPPORTED_WEBHOOK_EVENTS else "unsupported"


def _safe_message_origin(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "missing"
    return "bot" if value.strip().lower() == "bot" else "other"


def _safe_message_type(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "missing"
    message_type = value.strip().lower()
    return message_type if message_type in SUPPORTED_MESSAGE_TYPES else "unsupported"


def _non_empty_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _canonical_ticket_contact_external_id(data: Mapping[str, Any]) -> str | None:
    contact = data.get("contact")
    if not isinstance(contact, Mapping):
        return None
    contact_mapping = cast(Mapping[str, Any], contact)
    return _non_empty_string(contact_mapping.get("id"))


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


async def capture_contact_snapshot(
    payload: Mapping[str, Any], data: Mapping[str, Any]
) -> bool:
    raw_contact = data.get("contact")
    if not isinstance(raw_contact, Mapping):
        return False
    raw_contact = cast(Mapping[str, Any], raw_contact)
    try:
        contact = normalize_contact(raw_contact)
        observed_at, _has_timestamp = _ticket_event_timestamp(payload, data)
        await upsert_digisac_contact(
            contact,
            source="ticket_webhook",
            observed_at=observed_at,
        )
        return True
    except DigisacResponseError:
        logger.warning("DigiSac contact snapshot ignored: invalid contact shape")
    except Exception:
        logger.exception(
            "DigiSac contact snapshot persistence failed: contact_id=%s",
            _non_empty_string(raw_contact.get("id")) or "unknown",
        )
    return False


async def enqueue_audio_transcription(
    redis: AsyncRedis, message: DigisacMessage
) -> bool:
    """Persist an idempotent audio reservation for PostgreSQL polling."""
    del redis  # retained in the signature for webhook/test compatibility
    if message.message_type not in AUDIO_MESSAGE_TYPES or not message.message_id:
        return False
    reserved = await reserve_transcription(
        message.message_id,
        message.conversation_id,
        settings.audio_transcription_model,
    )
    if not reserved:
        return False
    return True


async def enqueue_image_extraction(
    redis: AsyncRedis, message: DigisacMessage
) -> bool:
    """Persist an idempotent image reservation for PostgreSQL polling."""
    del redis  # retained in the signature for webhook/test compatibility
    if not is_image_message(message.message_type, message.file) or not message.message_id:
        return False
    reserved = await reserve_image_extraction(
        message.message_id,
        message.conversation_id,
        settings.image_vision_model,
    )
    if not reserved:
        return False
    return True


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    require_admin_api_token()
    await initialize_database()
    app.state.redis = create_redis_client()
    directory_task = asyncio.create_task(directory_sync_loop())
    contact_hydration_task = asyncio.create_task(contact_hydration_loop())
    try:
        yield
    finally:
        directory_task.cancel()
        contact_hydration_task.cancel()
        with suppress(asyncio.CancelledError):
            await directory_task
        with suppress(asyncio.CancelledError):
            await contact_hydration_task
        await app.state.redis.aclose()
        await close_database()


app = FastAPI(title="Digisac Conversation Analyzer",
              version="1.0.0", lifespan=lifespan)
app.include_router(admin_router)
app.include_router(admin_ui_router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    path = request.url.path
    is_admin_command = path.startswith("/admin/acessorias/contacts/")
    is_ui_command = path.startswith("/admin/acessorias/ui/api/contacts/")
    is_confirm_or_reject = "/identity-links/" in path and (
        path.endswith("/confirm") or path.endswith("/reject")
    )
    if (is_admin_command or is_ui_command) and (
        is_confirm_or_reject or path.endswith("/identity-discovery")
    ):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Invalid administrative command body"},
            headers={"Cache-Control": "no-store"},
        )
    return await request_validation_exception_handler(request, exc)


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
    """Operational view of legacy lists and durable PostgreSQL work counts."""
    legacy_counts = await asyncio.gather(
        redis.llen("ia_queue"),
        redis.llen("ia_dead_letter"),
        redis.llen("audio_transcription_queue"),
        redis.llen("audio_transcription_dead_letter"),
        redis.llen("image_extraction_queue"),
        redis.llen("image_extraction_dead_letter"),
    )
    cycle_work, audio_work, image_work = await asyncio.gather(
        get_cycle_work_metrics(),
        get_transcription_work_metrics(),
        get_image_extraction_work_metrics(),
    )
    ia_queue, dead_letter, audio_queue, audio_dead_letter, image_queue, image_dead_letter = (
        legacy_counts
    )
    result: dict[str, Any] = {
        "ia_queue": ia_queue,
        "ia_dead_letter": dead_letter,
        "audio_transcription_queue": audio_queue,
        "audio_transcription_dead_letter": audio_dead_letter,
        "audio_due": audio_work["due"],
        "audio_scheduled": audio_work["scheduled"],
        "audio_leased": audio_work["leased"],
        "audio_stale": audio_work["stale"],
        "audio_completed": audio_work["completed"],
        "audio_failed": audio_work["failed"],
        "image_extraction_queue": image_queue,
        "image_extraction_dead_letter": image_dead_letter,
        "image_due": image_work["due"],
        "image_scheduled": image_work["scheduled"],
        "image_leased": image_work["leased"],
        "image_stale": image_work["stale"],
        "image_completed": image_work["completed"],
        "image_failed": image_work["failed"],
        "ia_due": cycle_work["due"],
        "ia_scheduled": cycle_work["scheduled"],
        "ia_leased": cycle_work["leased"],
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
        logger.warning("Digisac webhook rejected: reason=invalid_json")
        raise HTTPException(
            status_code=400, detail="Invalid JSON payload") from exc

    if not isinstance(parsed_payload, dict):
        raise HTTPException(
            status_code=400, detail="Webhook payload must be an object"
        )
    raw_payload = cast(dict[str, Any], parsed_payload)
    logger.info(
        "Digisac webhook parsed: event=%s top_level_key_count=%s",
        _safe_webhook_event(raw_payload.get("event")),
        len(raw_payload),
    )
    try:
        payload = WebhookPayload.model_validate(raw_payload)
    except ValueError as exc:
        logger.warning("Digisac webhook rejected: reason=validation_failed")
        raise HTTPException(
            status_code=400, detail="Webhook payload must be an object"
        ) from exc

    logger.info(
        "Digisac webhook field extraction: %s", payload.extraction_debug()
    )
    return raw_payload, payload


@app.post("/webhook/digisac", status_code=status.HTTP_202_ACCEPTED)
async def digisac_webhook(
    request: Request,
    response: Response,
    _: None = Depends(verify_webhook_signature),
    redis: AsyncRedis = Depends(get_redis),
) -> dict[str, Any]:
    """Ingest DigiSac events into the persistent cycle and media flows."""
    payload_data, _payload = await parse_webhook_payload(request)
    data = payload_data.get("data")
    if not isinstance(data, dict):
        logger.warning(
            "Digisac webhook rejected: event=%s data_type=%s "
            "top_level_key_count=%s",
            _safe_webhook_event(payload_data.get("event")),
            type(data).__name__,
            len(payload_data),
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
                "Ticket webhook ignored: missing data.id event=%s",
                _safe_webhook_event(event),
            )
            response.status_code = status.HTTP_200_OK
            return {"status": "ignored", "reason": "missing_ticket_id"}
        await capture_contact_snapshot(payload_data, data)
        if event == "ticket.created":
            timestamp, _has_timestamp = _ticket_event_timestamp(payload_data, data)
            cycle, created = await create_open_cycle(
                conversation_id=ticket_id,
                started_at=timestamp,
                open_event_key=_cycle_event_key(event, ticket_id, payload_data, data),
                start_strategy="ticket_created_event",
                contact_external_id=_canonical_ticket_contact_external_id(data),
            )
            return {
                "status": "ticket_created",
                "conversation_id": ticket_id,
                "cycle_id": str(cycle["public_id"]),
                "cycle_created": created,
            }
        await capture_ticket_assignment(payload_data, data, ticket_id)
        if data.get("isOpen") is True:
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
                contact_external_id=_canonical_ticket_contact_external_id(data),
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
        closed_at, _has_timestamp = _ticket_event_timestamp(payload_data, data)
        cycle, created = await close_cycle(
            conversation_id=ticket_id,
            protocol=protocol,
            closed_at=closed_at,
            close_event_key=_cycle_event_key(event, ticket_id, payload_data, data),
            contact_external_id=_canonical_ticket_contact_external_id(data),
        )
        if created:
            logger.info(
                "Cycle persisted for PostgreSQL finalization polling: "
                "cycle_id=%s conversation_id=%s",
                cycle["public_id"],
                ticket_id,
            )
        return {
            "status": "ticket_closed" if created else "ticket_already_closed",
            "conversation_id": ticket_id,
            "cycle_id": str(cycle["public_id"]),
            "queued": created,
        }

    if event not in {"message.created", "message.updated"}:
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored", "reason": "unsupported_event"}

    logger.info(
        "Message bot detection input",
        extra={
            "message_id": data.get("id"),
            "ticket_id": data.get("ticketId"),
            "is_from_bot": (
                data.get("isFromBot")
                if isinstance(data.get("isFromBot"), bool)
                else None
            ),
            "origin": _safe_message_origin(data.get("origin")),
            "is_from_me": (
                data.get("isFromMe")
                if isinstance(data.get("isFromMe"), bool)
                else None
            ),
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
                "is_from_bot": (
                    data.get("isFromBot")
                    if isinstance(data.get("isFromBot"), bool)
                    else None
                ),
                "origin": _safe_message_origin(data.get("origin")),
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
                    "message_type": _safe_message_type(data.get("type")),
                },
            )
        logger.info(
            "Digisac webhook ignored: event=%s reason=%s data_key_count=%s",
            _safe_webhook_event(payload_data.get("event")),
            adaptation.ignored_reason,
            len(data),
        )
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored", "reason": adaptation.ignored_reason}

    message = adaptation.message
    assert message is not None
    if message.sender_id:
        try:
            requested_at = (
                message.timestamp.isoformat() if message.timestamp else None
            )
            await request_contact_hydration(
                message.sender_id,
                requested_at=requested_at,
            )
        except Exception:
            logger.exception(
                "DigiSac contact hydration request failed: contact_id=%s",
                message.sender_id,
            )
    logger.info(
        "Message bot detection normalized",
        extra={
            "message_id": message.message_id,
            "ticket_id": message.conversation_id,
            "is_from_bot": message.is_from_bot,
            "origin": _safe_message_origin(message.origin),
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
    idempotency = IdempotencyService()
    event_id = idempotency.generate_event_id(idempotency_data)
    # Reserve before event idempotency so a duplicate webhook cannot erase a
    # durable media row. Audio admission itself does not publish to Redis.
    transcription_queued = await enqueue_audio_transcription(redis, message)
    image_extraction_queued = await enqueue_image_extraction(redis, message)
    if not await idempotency.try_mark_processed(event_id):
        return {"status": "duplicate", "conversation_id": conversation_id}
    return {
        "status": "received",
        "conversation_id": conversation_id,
        "transcription_queued": transcription_queued,
        "image_extraction_queued": image_extraction_queued,
    }


@app.get(
    "/conversations/{conversation_id}/status", response_model=ConversationProcessing
)
async def conversation_status(
    conversation_id: str,
) -> ConversationProcessing:
    cycle = await get_latest_cycle(conversation_id)
    if cycle is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationProcessing(
        conversation_id=conversation_id,
        cycle_id=str(cycle["public_id"]),
        status=cycle["status"],
        started_at=cycle["created_at"],
        completed_at=cycle.get("completed_at"),
        error_message=cycle.get("error_message"),
        retry_count=cycle["attempt_count"],
        transient_retry_count=cycle.get("transient_retry_count", 0),
        max_retries=settings.max_retry_attempts,
    )


@app.get("/conversations/{conversation_id}/result")
async def conversation_result(
    conversation_id: str,
) -> Any:
    cycle = await get_latest_cycle(conversation_id)
    if cycle is None:
        raise HTTPException(status_code=404, detail="Result not available")
    result = await get_cycle_result(str(cycle["public_id"]))
    if not result or not result.get("classification_public_id"):
        raise HTTPException(status_code=404, detail="Result not available")
    return result


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


install_openapi_contract(app)
