"""Pure normalization, filtering, snapshot and rendering for finalization."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from src.core.config import settings
from src.core.message_filter import is_bot_message
from src.core.media import effective_message_type, is_image_message


DISPLAY_TIMEZONE = ZoneInfo("America/Sao_Paulo")
SUPPORTED_HISTORY_TYPES = {"chat", "document", "ptt", "audio", "voice", "image"}
AUDIO_TYPES = {"ptt", "audio", "voice"}
CLOCK_SKEW = timedelta(seconds=5)


@dataclass(frozen=True)
class NormalizedHistory:
    messages: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    exclusion_counts: dict[str, int]
    cycle_started_at: datetime
    cycle_start_strategy: str


def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def safe_file_metadata(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, str] = {}
    for source, target in (
        ("id", "id"),
        ("name", "name"),
        ("publicFilename", "public_filename"),
        ("public_filename", "public_filename"),
        ("extension", "extension"),
        ("mimetype", "mimetype"),
    ):
        value = raw.get(source)
        if isinstance(value, str) and value.strip() and target not in result:
            result[target] = value.strip()
    return result


def infer_cycle_start(
    raw_messages: Sequence[Mapping[str, Any]],
    *,
    configured_start: datetime | None,
    ticket_started_at: Any,
    previous_closed_at: datetime | None,
    closed_at: datetime,
) -> tuple[datetime, str]:
    if configured_start is not None:
        return configured_start, "webhook_open_event"
    technical_opens: list[datetime] = []
    valid_messages: list[datetime] = []
    for message in raw_messages:
        timestamp = parse_timestamp(
            message.get("timestamp") or message.get("createdAt")
        )
        if timestamp is None or timestamp > closed_at + CLOCK_SKEW:
            continue
        data = message.get("data")
        if (
            message.get("type") == "ticket"
            and isinstance(data, Mapping)
            and data.get("ticketOpen") is True
        ):
            technical_opens.append(timestamp)
        elif message.get("type") in SUPPORTED_HISTORY_TYPES:
            if previous_closed_at is None or timestamp > previous_closed_at:
                valid_messages.append(timestamp)
    if technical_opens:
        return max(technical_opens), "digisac_ticket_open_event"
    ticket_start = parse_timestamp(ticket_started_at)
    if (
        ticket_start is not None
        and ticket_start <= closed_at
        and (previous_closed_at is None or ticket_start > previous_closed_at)
    ):
        return ticket_start, "digisac_ticket_started_at"
    if valid_messages:
        return min(valid_messages), "first_unassociated_message"
    return closed_at, "empty_cycle_at_close"


def normalize_history(
    raw_messages: Sequence[Mapping[str, Any]],
    *,
    cycle_started_at: datetime | None,
    previous_closed_at: datetime | None,
    ticket_closed_at: datetime,
    ticket_started_at: Any = None,
) -> NormalizedHistory:
    inferred_start, strategy = infer_cycle_start(
        raw_messages,
        configured_start=cycle_started_at,
        ticket_started_at=ticket_started_at,
        previous_closed_at=previous_closed_at,
        closed_at=ticket_closed_at,
    )
    warnings: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}
    normalized: list[dict[str, Any]] = []

    def exclude(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    for raw in raw_messages:
        message_id = raw.get("id")
        if not isinstance(message_id, str) or not message_id.strip():
            exclude("missing_message_id")
            continue
        message_type = raw.get("type")
        if message_type == "ticket":
            exclude("technical_ticket_event")
            continue
        if message_type not in SUPPORTED_HISTORY_TYPES:
            exclude("unknown_type")
            warnings.append(
                {
                    "code": "unknown_message_type",
                    "message_id": message_id,
                    "type": str(message_type),
                }
            )
            continue
        if raw.get("visible") is False:
            exclude("invisible")
            continue
        if raw.get("deletedAt") not in (None, ""):
            exclude("deleted")
            continue
        if is_bot_message(
            is_from_bot=(
                raw.get("isFromBot") if isinstance(raw.get("isFromBot"), bool) else None
            ),
            origin=raw.get("origin") if isinstance(raw.get("origin"), str) else None,
        ):
            exclude("bot")
            continue
        timestamp = parse_timestamp(raw.get("timestamp") or raw.get("createdAt"))
        if timestamp is None:
            exclude("invalid_timestamp")
            warnings.append({"code": "invalid_timestamp", "message_id": message_id})
            continue
        lower = inferred_start - CLOCK_SKEW
        if previous_closed_at is not None and timestamp <= previous_closed_at:
            exclude("previous_cycle")
            continue
        if timestamp < lower or timestamp > ticket_closed_at + CLOCK_SKEW:
            exclude("outside_cycle")
            continue
        is_from_me = raw.get("isFromMe") is True
        text = raw.get("text")
        file_data = safe_file_metadata(raw.get("file"))
        normalized.append(
            {
                "message_id": message_id,
                "timestamp": timestamp.isoformat(),
                "sender_type": "agent" if is_from_me else "client",
                "sender_id": (
                    raw.get("userId") if is_from_me else raw.get("contactId")
                ),
                "sender_name": None,
                "type": effective_message_type(message_type, file_data),
                "content": text.strip() if isinstance(text, str) else "",
                "quoted_message_id": (
                    raw.get("quotedMessageId")
                    if isinstance(raw.get("quotedMessageId"), str)
                    else None
                ),
                "media_status": None,
                "file": file_data,
            }
        )
    normalized.sort(key=lambda item: (item["timestamp"], item["message_id"]))
    return NormalizedHistory(
        messages=normalized,
        warnings=warnings,
        exclusion_counts=excluded,
        cycle_started_at=inferred_start,
        cycle_start_strategy=strategy,
    )


def apply_media_states(
    messages: Sequence[Mapping[str, Any]],
    states: Mapping[str, Mapping[str, Any]],
    *,
    max_attempts: int,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    set[str],
    set[str],
]:
    output: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    pending: set[str] = set()
    blocked: set[str] = set()
    for original in messages:
        message = dict(original)
        message_id = str(message["message_id"])
        message_type = str(message["type"])
        if message_type not in AUDIO_TYPES and not is_image_message(
            message_type, message.get("file")
        ):
            output.append(message)
            continue
        state = states.get(message_id)
        if state is None:
            message["media_status"] = "missing"
            pending.add(message_id)
        else:
            status = state.get("status")
            attempts = int(state.get("attempt_count") or 0)
            message["media_status"] = status
            if status == "completed":
                text = state.get("text")
                message["content"] = text.strip() if isinstance(text, str) else ""
            elif status in {"pending", "processing"}:
                pending.add(message_id)
            elif status == "failed" and attempts < max_attempts:
                pending.add(message_id)
            elif (
                status == "failed"
                and is_image_message(message_type, message.get("file"))
                and attempts >= max_attempts
            ):
                blocked.add(message_id)
            elif status == "failed":
                kind = "audio" if message_type in AUDIO_TYPES else "image"
                message["content"] = (
                    "[ÁUDIO NÃO DISPONÍVEL — processamento falhou após "
                    f"{attempts} tentativas]"
                    if kind == "audio"
                    else "[IMAGEM NÃO DISPONÍVEL — processamento falhou após "
                    f"{attempts} tentativas]"
                )
                warnings.append(
                    {
                        "code": "media_failed",
                        "message_id": message_id,
                        "kind": kind,
                        "attempt_count": attempts,
                    }
                )
        output.append(message)
    return output, warnings, pending, blocked


def _quote_excerpt(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    text = content.strip() if isinstance(content, str) else ""
    if not text:
        message_type = message.get("type")
        text = f"mensagem {message_type}" if message_type else "mensagem indisponível"
    text = re.sub(r"\s+", " ", text)
    limit = settings.quoted_message_max_chars
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def render_context(
    *,
    ticket_id: str,
    protocol: str | None,
    departments: Sequence[str],
    agents: Sequence[str],
    messages: Sequence[Mapping[str, Any]],
    include_administrative_names: bool,
) -> str:
    headers = [
        f"PROTOCOLO: {protocol or 'NÃO INFORMADO'}",
        f"TICKET_ID: {ticket_id}",
    ]
    if include_administrative_names:
        headers.extend(
            [
                "DEPARTAMENTOS: " + (", ".join(departments) or "NÃO INFORMADO"),
                "ATENDENTES: " + (", ".join(agents) or "NÃO INFORMADO"),
            ]
        )
    by_id = {str(item["message_id"]): item for item in messages}
    blocks: list[str] = []
    for message in messages:
        timestamp = parse_timestamp(message.get("timestamp"))
        if timestamp is None:
            continue
        local = timestamp.astimezone(DISPLAY_TIMEZONE)
        role = "ATENDENTE" if message.get("sender_type") == "agent" else "CLIENTE"
        name = message.get("sender_name")
        label = role + (f" — {name}" if isinstance(name, str) and name else "")
        message_type = message.get("type")
        content = message.get("content")
        text = content.strip() if isinstance(content, str) else ""
        quoted_id = message.get("quoted_message_id")
        quote_line = ""
        if isinstance(quoted_id, str) and quoted_id:
            quoted = by_id.get(quoted_id)
            excerpt = (
                _quote_excerpt(quoted)
                if quoted is not None
                else "mensagem citada indisponível"
            )
            quote_line = f"[EM RESPOSTA A: “{excerpt}”]\n"
        if is_image_message(message_type, message.get("file")):
            label += " — IMAGEM ANALISADA"
            text = text or f"[{role} ENVIOU UMA IMAGEM]"
        elif message_type == "document":
            file_data = message.get("file")
            filename = (
                file_data.get("name") or file_data.get("public_filename")
                if isinstance(file_data, Mapping)
                else None
            )
            text = (
                f"[{role} ENVIOU O DOCUMENTO: {filename}]"
                if filename
                else f"[{role} ENVIOU UM DOCUMENTO]"
            )
            label += " — DOCUMENTO"
        elif message_type in AUDIO_TYPES:
            label += " — ÁUDIO TRANSCRITO"
            text = text or f"[{role} ENVIOU UM ÁUDIO]"
        if not text:
            continue
        blocks.append(
            f"[{local.strftime('%d/%m/%Y %H:%M:%S %z')}] {label}\n"
            f"{quote_line}{text}"
        )
    return "\n".join(headers) + "\n\n" + "\n\n".join(blocks)


def estimate_tokens(text: str) -> int:
    """Conservative dependency-free estimate for Portuguese UTF-8 text."""
    return math.ceil(len(text.encode("utf-8")) / 3)


def chunk_messages(
    messages: Sequence[Mapping[str, Any]], *, token_limit: int
) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0
    for raw in messages:
        message = dict(raw)
        cost = estimate_tokens(str(message.get("content") or "")) + 40
        if current and current_tokens + cost > token_limit:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(message)
        current_tokens += cost
    if current:
        chunks.append(current)
    return chunks
