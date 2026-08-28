import os
import json
import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, cast
from groq import Groq

from src.core.config import settings
from src.core.analysis import with_protocol
from src.core.acessorias_requests import create_request_for_cycle
from src.core.acessorias_preparation import prepare_cycle_for_request
from src.core.db import (  # noqa: F401
    close_database,
    get_pending_content_extractions,
    get_content_states,
    get_previous_cycle,
    get_recoverable_cycles,
    initialize_database,
    insert_classification,
    claim_cycle,
    reserve_image_extraction,
    reserve_transcription,
    release_cycle_publication,
    release_image_publication,
    release_transcription_publication,
    resolve_user_names,
    resolve_ticket_assignments,
    save_cycle_messages,
    transition_cycle,
    wake_unblocked_media_cycles,
)
from src.core.digisac_client import DigisacClient, DigisacHistory
from src.core.digisac_directory import sync_digisac_directories
from src.core.media import is_image_message
from src.core.finalization import (
    AUDIO_TYPES,
    apply_media_states,
    chunk_messages,
    estimate_tokens,
    normalize_history,
    parse_timestamp,
    render_context,
)
from src.core.ia_classification import build_prompt, parse_result, system_prompt
from src.core.redis_client import AsyncRedis, create_redis_client
from src.core.provider_retry import (
    ProviderRetryWindowActive,
    TransientProviderError,
    provider_cooldown_delay,
    retry_delay,
    transient_provider_error,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class IAWorker:
    def __init__(self, redis_client: AsyncRedis):
        self.redis = redis_client
        self.queue = "ia_queue"
        self.dead_letter = "ia_dead_letter"

        # Usa GROQ_API_KEY
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY is required to run the IA worker")

        # Inicializa cliente Groq
        self.client = Groq(api_key=api_key, max_retries=0)
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
        self.provider_blocked_until = 0.0
        self.provider_window_resume_pending = False

    async def process(self) -> None:
        """Process persistent conversation-cycle jobs from Redis."""
        logger.info("🤖 IA Worker started with Groq")
        await self._process_cycle_queue()

    async def _process_cycle_queue(self) -> None:
        owner = f"{uuid.uuid4().hex}:{os.getpid()}"
        next_reconcile = 0.0
        logger.info("Persistent conversation finalization worker started")
        while True:
            item: str | None = None
            try:
                if time.monotonic() >= next_reconcile:
                    await self._reconcile_cycles()
                    next_reconcile = (
                        time.monotonic()
                        + settings.finalization_reconcile_interval_seconds
                    )
                if await self._pause_for_provider_window():
                    continue
                item = await self.redis.lpop(self.queue)
                if not item:
                    await asyncio.sleep(0.5)
                    continue
                parsed: Any = json.loads(item)
                if not isinstance(parsed, dict):
                    raise ValueError("finalization job must be a JSON object")
                job = cast(dict[str, Any], parsed)
                cycle_id = job.get("cycle_id")
                if not isinstance(cycle_id, str) or not cycle_id:
                    raise ValueError("finalization job is missing cycle_id")
                not_before = float(job.get("not_before", 0) or 0)
                if not_before > time.time():
                    await self.redis.rpush(self.queue, item)
                    await asyncio.sleep(min(not_before - time.time(), 1.0))
                    item = None
                    continue
                provider_remaining = self._provider_window_remaining()
                if provider_remaining > 0:
                    await self.redis.rpush(self.queue, item)
                    item = None
                    await asyncio.sleep(min(provider_remaining, 1.0))
                    continue
                self._log_provider_window_resumed()
                cycle = await claim_cycle(
                    cycle_id,
                    owner=owner,
                    lease_seconds=settings.finalization_lease_seconds,
                )
                if cycle is None:
                    continue
                try:
                    await self._process_cycle(cycle, job)
                except Exception as exc:
                    await self._record_cycle_failure(cycle, job, exc)
            except Exception:
                logger.exception("Finalization worker loop failed")
                if item:
                    await self.redis.rpush(self.dead_letter, item)
                await asyncio.sleep(1)

    async def _reconcile_cycles(self) -> None:
        awakened = await wake_unblocked_media_cycles(
            max_attempts=self.max_retries,
            limit=100,
        )
        if awakened:
            logger.info(
                "Media-blocked cycles became recoverable: count=%s",
                len(awakened),
            )
        cycles = await get_recoverable_cycles(limit=100)
        published = 0
        for cycle in cycles:
            cycle_id = str(cycle["public_id"])
            job = {
                "cycle_id": cycle_id,
                "conversation_id": cycle["conversation_id"],
                "protocol": cycle.get("protocol"),
                "attempt": cycle["attempt_count"],
                "not_before": (
                    datetime.fromisoformat(str(cycle["next_attempt_at"])).timestamp()
                    if cycle.get("next_attempt_at")
                    else 0
                ),
            }
            try:
                await self.redis.rpush(
                    self.queue, json.dumps(job, ensure_ascii=False)
                )
                published += 1
            except Exception:
                await release_cycle_publication(cycle_id)
                logger.exception(
                    "Failed to republish recoverable cycle: cycle_id=%s",
                    cycle_id,
                )
        if published:
            logger.debug("Published due finalization cycles: count=%s", published)

    async def _prepare_and_create_request(
        self, cycle_id: str
    ) -> dict[str, Any] | None:
        preparation = await prepare_cycle_for_request(cycle_id)
        if not preparation.ready:
            logger.info(
                "Acessórias Request preparation blocked: cycle_id=%s stage=%s reason=%s",
                cycle_id,
                preparation.stage,
                preparation.reason,
            )
            return None
        return await create_request_for_cycle(cycle_id)

    async def _recover_history(self, conversation_id: str) -> DigisacHistory:
        client = DigisacClient()
        last: DigisacHistory | None = None
        for attempt in range(1, settings.digisac_history_max_attempts + 1):
            last = await asyncio.to_thread(
                client.get_ticket_history, conversation_id
            )
            if last.complete:
                return last
            logger.warning(
                "DigiSac history is not consistent yet: "
                "conversation_id=%s attempt=%s reasons=%s",
                conversation_id,
                attempt,
                last.consistency_reasons,
            )
            if attempt < settings.digisac_history_max_attempts:
                await asyncio.sleep(
                    settings.digisac_history_retry_base_seconds
                    * (2 ** (attempt - 1))
                )
        assert last is not None
        raise RuntimeError(
            "DigiSac history remained inconsistent: "
            + ",".join(last.consistency_reasons)
        )

    async def _ensure_media_jobs(
        self,
        conversation_id: str,
        messages: list[dict[str, Any]],
        states: dict[str, dict[str, Any]],
    ) -> None:
        for message in messages:
            message_id = str(message["message_id"])
            message_type = str(message["type"])
            if message_type not in AUDIO_TYPES and not is_image_message(
                message_type, message.get("file")
            ):
                continue
            state = states.get(message_id)
            if state and (
                state.get("status") in {"completed", "pending", "processing"}
                or (
                    state.get("status") == "failed"
                    and int(state.get("attempt_count") or 0)
                    >= self.max_retries
                )
            ):
                continue
            if message_type in AUDIO_TYPES:
                reserved = await reserve_transcription(
                    message_id,
                    conversation_id,
                    settings.audio_transcription_model,
                )
                queue = "audio_transcription_queue"
            else:
                reserved = await reserve_image_extraction(
                    message_id,
                    conversation_id,
                    settings.image_vision_model,
                )
                queue = "image_extraction_queue"
            if reserved:
                try:
                    await self.redis.rpush(
                        queue,
                        json.dumps(
                            {
                                "message_id": message_id,
                                "conversation_id": conversation_id,
                                "attempt": (
                                    int(state.get("attempt_count") or 0)
                                    if state
                                    else 0
                                ),
                            }
                        ),
                    )
                except Exception as exc:
                    error = f"queue publish failed: {exc}"
                    if message_type in AUDIO_TYPES:
                        await release_transcription_publication(
                            message_id, error
                        )
                    else:
                        await release_image_publication(message_id, error)
                    raise

    def _next_media_check_at(
        self,
        pending: set[str],
        states: dict[str, dict[str, Any]],
    ) -> datetime:
        now = datetime.now(timezone.utc)
        fallback = now + timedelta(
            seconds=settings.media_status_recheck_seconds
        )
        candidates: list[datetime] = []
        needs_fallback = False
        for message_id in pending:
            state = states.get(message_id)
            scheduled = parse_timestamp(
                state.get("next_attempt_at") if state else None
            )
            if scheduled is None or scheduled <= now:
                needs_fallback = True
            else:
                candidates.append(scheduled)
        if needs_fallback or not candidates:
            candidates.append(fallback)
        return min(candidates)

    @staticmethod
    def _media_wait_fingerprint(
        message_ids: set[str],
        states: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "message_id": message_id,
                "status": states.get(message_id, {}).get("status", "missing"),
                "attempt_count": int(
                    states.get(message_id, {}).get("attempt_count") or 0
                ),
            }
            for message_id in sorted(message_ids)
        ]

    async def _process_cycle(
        self, cycle: dict[str, Any], job: dict[str, Any]
    ) -> None:
        cycle_id = str(cycle["public_id"])
        conversation_id = str(cycle["conversation_id"])
        started = time.perf_counter()
        snapshot = cycle.get("snapshot_json")
        if not isinstance(snapshot, dict) or not cast(
            dict[str, Any], snapshot
        ).get("history_recovery"):
            await transition_cycle(
                cycle_id,
                "recovering_messages",
                expected_statuses=(
                    "pending",
                    "retryable_failure",
                    "recovering_messages",
                ),
                fields={
                    "history_recovery_attempt": int(
                        cycle.get("history_recovery_attempt") or 0
                    )
                    + 1,
                    "error_phase": None,
                    "error_message": None,
                },
            )
            history = await self._recover_history(conversation_id)
            closed_at = parse_timestamp(cycle.get("ticket_closed_at"))
            if closed_at is None:
                raise RuntimeError("cycle does not have a valid close timestamp")
            configured_start = parse_timestamp(cycle.get("cycle_started_at"))
            previous = await get_previous_cycle(
                conversation_id, int(cycle["sequence_number"])
            )
            previous_closed = (
                parse_timestamp(previous.get("ticket_closed_at"))
                if previous
                else None
            )
            normalized = normalize_history(
                history.messages,
                cycle_started_at=configured_start,
                previous_closed_at=previous_closed,
                ticket_closed_at=closed_at,
                ticket_started_at=history.ticket.get("startedAt"),
            )
            accepted, conflicts = await save_cycle_messages(
                cycle_id, normalized.messages
            )
            accepted_set = set(accepted)
            messages = [
                item
                for item in normalized.messages
                if item["message_id"] in accepted_set
            ]
            warnings = list(normalized.warnings)
            warnings.extend(
                {"code": "message_owned_by_other_cycle", "message_id": item}
                for item in conflicts
            )
            departments, agents = (await self._resolve_assignment_names(
                conversation_id
            ))
            user_names = await resolve_user_names(
                [
                    str(item["sender_id"])
                    for item in messages
                    if item.get("sender_type") == "agent"
                    and item.get("sender_id")
                ]
            )
            for message in messages:
                sender_id = message.get("sender_id")
                if message.get("sender_type") == "agent" and sender_id:
                    message["sender_name"] = user_names.get(str(sender_id))
            snapshot = {
                "cycle_id": cycle_id,
                "ticket_id": conversation_id,
                "protocol": cycle.get("protocol"),
                "cycle_started_at": normalized.cycle_started_at.isoformat(),
                "ticket_closed_at": closed_at.isoformat(),
                "cycle_start_strategy": normalized.cycle_start_strategy,
                "timezone": "America/Sao_Paulo",
                "departments": departments,
                "agents": agents,
                "messages": messages,
                "warnings": warnings,
                "filter_summary": normalized.exclusion_counts,
                "history_recovery": {
                    "attempt_count": int(
                        cycle.get("history_recovery_attempt") or 0
                    )
                    + 1,
                    "page_count": history.page_count,
                    "total": history.total,
                    "duplicate_count": history.duplicate_count,
                    "complete": history.complete,
                },
            }
            await transition_cycle(
                cycle_id,
                "building_context",
                expected_statuses=("recovering_messages",),
                fields={
                    "cycle_started_at": normalized.cycle_started_at,
                    "cycle_start_strategy": normalized.cycle_start_strategy,
                    "snapshot_json": snapshot,
                    "warning_count": len(warnings),
                    "history_page_count": history.page_count,
                },
            )
        messages = [
            dict(item)
            for item in cast(dict[str, Any], snapshot).get("messages", [])
        ]
        audio_ids = [
            str(item["message_id"])
            for item in messages
            if item.get("type") in AUDIO_TYPES
        ]
        image_ids = [
            str(item["message_id"])
            for item in messages
            if is_image_message(item.get("type"), item.get("file"))
        ]
        states = await get_content_states(audio_ids, image_ids)
        await self._ensure_media_jobs(conversation_id, messages, states)
        states = await get_content_states(audio_ids, image_ids)
        hydrated, media_warnings, pending, blocked = apply_media_states(
            messages, states, max_attempts=self.max_retries
        )
        snapshot = cast(dict[str, Any], snapshot)
        if blocked:
            fingerprint = self._media_wait_fingerprint(blocked, states)
            previous_raw = snapshot.get("media_wait")
            previous = (
                cast(dict[str, Any], previous_raw)
                if isinstance(previous_raw, dict)
                else None
            )
            changed = (
                previous is None
                or previous.get("fingerprint") != fingerprint
                or previous.get("blocked") is not True
            )
            snapshot["media_wait"] = {
                "blocked": True,
                "fingerprint": fingerprint,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            await transition_cycle(
                cycle_id,
                "media_blocked",
                expected_statuses=(
                    "building_context",
                    "waiting_media",
                    "retryable_failure",
                ),
                fields={
                    "snapshot_json": snapshot,
                    "next_attempt_at": None,
                    "enqueued_at": None,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "error_phase": "media",
                    "error_message": (
                        "media extraction requires intervention: "
                        + ",".join(
                            f"{states.get(message_id, {}).get('kind', 'media')}:{message_id}"
                            for message_id in sorted(blocked)
                        )
                    ),
                },
            )
            if changed:
                logger.warning(
                    "Cycle blocked by failed media extraction: "
                    "cycle_id=%s conversation_id=%s media_ids=%s",
                    cycle_id,
                    conversation_id,
                    sorted(blocked),
                )
            return
        if pending:
            not_before = self._next_media_check_at(pending, states)
            fingerprint = self._media_wait_fingerprint(pending, states)
            previous_raw = snapshot.get("media_wait")
            previous = (
                cast(dict[str, Any], previous_raw)
                if isinstance(previous_raw, dict)
                else None
            )
            changed = (
                previous is None
                or previous.get("fingerprint") != fingerprint
                or previous.get("blocked") is not False
            )
            snapshot["media_wait"] = {
                "blocked": False,
                "fingerprint": fingerprint,
                "next_check_at": not_before.isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            updated = await transition_cycle(
                cycle_id,
                "waiting_media",
                expected_statuses=(
                    "building_context",
                    "waiting_media",
                    "retryable_failure",
                ),
                fields={
                    "snapshot_json": snapshot,
                    "next_attempt_at": not_before,
                    "enqueued_at": None,
                    "lease_owner": None,
                    "lease_expires_at": None,
                },
            )
            if updated and changed:
                logger.info(
                    "Cycle waiting for media: cycle_id=%s conversation_id=%s "
                    "pending_media_count=%s next_check_at=%s",
                    cycle_id,
                    conversation_id,
                    len(pending),
                    not_before.isoformat(),
                )
            elif updated:
                logger.debug(
                    "Cycle still waiting for unchanged media: cycle_id=%s "
                    "conversation_id=%s pending_media_count=%s next_check_at=%s",
                    cycle_id,
                    conversation_id,
                    len(pending),
                    not_before.isoformat(),
                )
            return
        snapshot.pop("media_wait", None)
        snapshot["messages"] = hydrated
        snapshot["warnings"] = list(snapshot.get("warnings", [])) + media_warnings
        departments = [
            str(item) for item in snapshot.get("departments", [])
        ]
        agents = [str(item) for item in snapshot.get("agents", [])]
        rendered = render_context(
            ticket_id=conversation_id,
            protocol=cycle.get("protocol"),
            departments=departments,
            agents=agents,
            messages=hydrated,
            include_administrative_names=True,
        )
        model_context = render_context(
            ticket_id=conversation_id,
            protocol=cycle.get("protocol"),
            departments=departments,
            agents=agents,
            messages=hydrated,
            include_administrative_names=False,
        )
        reductions: list[dict[str, Any]] = []
        reduction_applied = estimate_tokens(model_context) > (
            settings.ia_context_safe_input_tokens
        )
        if reduction_applied:
            await transition_cycle(
                cycle_id,
                "summarizing",
                fields={"snapshot_json": snapshot},
            )
            summaries: list[str] = []
            for index, chunk in enumerate(
                chunk_messages(
                    hydrated, token_limit=settings.ia_context_chunk_tokens
                ),
                start=1,
            ):
                summary = await self._summarize_messages(chunk)
                ids = [str(item["message_id"]) for item in chunk]
                reductions.append(
                    {
                        "chunk": index,
                        "message_ids": ids,
                        "summary": summary,
                    }
                )
                summaries.append(
                    f"BLOCO {index} ({ids[0]}..{ids[-1]}):\n{summary}"
                )
            model_context = (
                f"PROTOCOLO: {cycle.get('protocol') or 'NÃO INFORMADO'}\n"
                f"TICKET_ID: {conversation_id}\n\n"
                + "\n\n".join(summaries)
            )
            if estimate_tokens(model_context) > (
                settings.ia_context_safe_input_tokens
            ):
                raise RuntimeError(
                    "reduced context still exceeds safe input token limit"
                )
        await transition_cycle(
            cycle_id,
            "classifying",
            fields={
                "snapshot_json": snapshot,
                "rendered_context": rendered,
                "model_context": model_context,
                "context_reduction_applied": reduction_applied,
                "context_reduction_json": reductions,
                "warning_count": len(snapshot["warnings"]),
            },
        )
        if hydrated:
            result = await self._analyze_with_groq(model_context, len(hydrated))
        else:
            result = {
                "intent_type": "other",
                "confidence": 0.0,
                "title": "Sem conteúdo para análise",
                "description": "O ciclo não possui mensagens humanas classificáveis.",
                "message_count": 0,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }
            snapshot["warnings"].append({"code": "no_classifiable_messages"})
        result = {
            **result,
            "department": departments,
            "agent": agents,
        }
        processing_ms = int((time.perf_counter() - started) * 1000)
        identity = await insert_classification(
            conversation_id=conversation_id,
            message_ids=[str(item["message_id"]) for item in hydrated],
            created_at=result["processed_at"],
            full_context=model_context,
            message_count=len(hydrated),
            result=result,
            model=self.model,
            processing_time_ms=processing_ms,
            prompt_version=self.prompt_version,
            protocol=cycle.get("protocol"),
            idempotency_key=f"ia:cycle:{cycle_id}",
        )
        final_status = (
            "completed_with_warnings"
            if snapshot["warnings"]
            else "completed"
        )
        await transition_cycle(
            cycle_id,
            final_status,
            expected_statuses=("classifying",),
            fields={
                "classification_id": identity.id,
                "snapshot_json": snapshot,
                "warning_count": len(snapshot["warnings"]),
                "processing_time_ms": processing_ms,
                "completed_at": datetime.now(timezone.utc),
                "next_attempt_at": None,
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )
        try:
            request_operation = await self._prepare_and_create_request(cycle_id)
            if request_operation is not None:
                logger.info(
                    "Acessórias Request operation evaluated: cycle_id=%s state=%s "
                    "failure_category=%s",
                    cycle_id,
                    request_operation.get("state"),
                    request_operation.get("failure_category"),
                )
        except Exception as exc:
            # Request delivery is a separate durable operation. A provider or
            # reconciliation failure must never roll back the classification.
            logger.error(
                "Acessórias Request operation could not be evaluated: "
                "cycle_id=%s error_type=%s",
                cycle_id,
                type(exc).__name__,
            )
        result = with_protocol(result, cycle.get("protocol"))
        if identity.public_id is not None:
            result["classification_public_id"] = str(identity.public_id)
        result["cycle_id"] = cycle_id
        await self.redis.set(
            f"ia_result:{conversation_id}",
            json.dumps(result, ensure_ascii=False),
            ex=self.result_ttl_seconds,
        )
        await self.redis.set(
            f"ia_status:{conversation_id}",
            json.dumps(
                {
                    "conversation_id": conversation_id,
                    "cycle_id": cycle_id,
                    "status": final_status,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
            ex=self.result_ttl_seconds,
        )
        logger.info(
            "Conversation cycle completed: cycle_id=%s conversation_id=%s "
            "status=%s message_count=%s audio_count=%s image_count=%s "
            "context_estimated_tokens=%s context_reduction_applied=%s "
            "classification_id=%s processing_time_ms=%s",
            cycle_id,
            conversation_id,
            final_status,
            len(hydrated),
            len(audio_ids),
            len(image_ids),
            estimate_tokens(model_context),
            reduction_applied,
            identity.id,
            processing_ms,
        )

    async def _summarize_messages(
        self, messages: list[dict[str, Any]]
    ) -> str:
        content = "\n\n".join(
            f"{item.get('timestamp')} {item.get('sender_type')}: "
            f"{item.get('content')}"
            for item in messages
        )
        self._ensure_provider_available()
        try:
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Resuma fielmente este trecho de atendimento, sem inventar. "
                            "Preserve dúvidas, problemas, solicitações, respostas, itens "
                            "não resolvidos, valores, datas, competências, prazos, "
                            "documentos, protocolos e compromissos."
                        ),
                    },
                    {"role": "user", "content": content},
                ],
                temperature=0,
                max_completion_tokens=settings.ia_context_summary_output_tokens,
            )
        except Exception as exc:
            transient = transient_provider_error(exc, "Groq summary request")
            if transient is not None:
                raise transient from exc
            raise
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise RuntimeError("summary response exceeded the output limit")
        summary = getattr(choice.message, "content", None)
        if not isinstance(summary, str) or not summary.strip():
            raise RuntimeError("summary response is empty")
        return summary.strip()

    def _provider_window_remaining(self) -> float:
        return max(
            0.0,
            getattr(self, "provider_blocked_until", 0.0) - time.time(),
        )

    def _log_provider_window_resumed(self) -> None:
        if (
            self._provider_window_remaining() <= 0
            and getattr(self, "provider_window_resume_pending", False)
        ):
            self.provider_window_resume_pending = False
            logger.info(
                "Groq provider retry window ended; queue consumption resumed"
            )

    async def _pause_for_provider_window(self) -> bool:
        remaining = self._provider_window_remaining()
        if remaining <= 0:
            self._log_provider_window_resumed()
            return False
        await asyncio.sleep(min(remaining, 1.0))
        return True

    def _open_provider_window(self, delay: float, *, now: float) -> None:
        was_active = self._provider_window_remaining() > 0
        self.provider_blocked_until = max(
            self.provider_blocked_until,
            now + delay,
        )
        self.provider_window_resume_pending = True
        if not was_active:
            logger.warning(
                "Groq provider retry window opened: "
                "provider_cooldown_delay=%.3fs",
                delay,
            )

    def _ensure_provider_available(self) -> None:
        remaining = self._provider_window_remaining()
        if remaining > 0:
            raise ProviderRetryWindowActive(
                retry_after_seconds=remaining
            )

    async def _record_cycle_failure(
        self,
        cycle: dict[str, Any],
        job: dict[str, Any],
        exc: Exception,
    ) -> None:
        cycle_id = str(cycle["public_id"])
        if isinstance(exc, ProviderRetryWindowActive):
            logger.debug(
                "Local provider retry guard reached after cycle claim; "
                "cycle state left unchanged: cycle_id=%s conversation_id=%s "
                "provider_retry_after=%s provider_call_attempted=false",
                cycle_id,
                cycle.get("conversation_id"),
                exc.retry_after_seconds,
            )
            return
        sanitized = f"{type(exc).__name__}: {str(exc)[:500]}"
        if isinstance(exc, TransientProviderError):
            transient_attempt = (
                int(cycle.get("transient_retry_count") or 0) + 1
            )
            cycle_retry_delay = retry_delay(
                attempt=transient_attempt,
                base_seconds=settings.ia_retry_base_seconds,
                max_delay_seconds=settings.ia_retry_max_delay_seconds,
                provider_margin_seconds=(
                    settings.ia_retry_provider_margin_seconds
                ),
                provider_delay_seconds=exc.retry_after_seconds,
            )
            provider_delay = provider_cooldown_delay(
                base_seconds=settings.ia_retry_base_seconds,
                provider_margin_seconds=(
                    settings.ia_retry_provider_margin_seconds
                ),
                provider_delay_seconds=exc.retry_after_seconds,
            )
            now = time.time()
            self._open_provider_window(provider_delay, now=now)
            not_before = datetime.fromtimestamp(
                now + cycle_retry_delay,
                tz=timezone.utc,
            )
            await transition_cycle(
                cycle_id,
                "retryable_failure",
                fields={
                    "transient_retry_count": transient_attempt,
                    "error_phase": str(cycle.get("status")),
                    "error_message": sanitized,
                    "next_attempt_at": not_before,
                    "enqueued_at": None,
                    "lease_owner": None,
                    "lease_expires_at": None,
                },
            )
            logger.warning(
                "Conversation cycle provider retry scheduled: cycle_id=%s "
                "conversation_id=%s transient_retry_count=%s "
                "cycle_retry_delay=%.3fs provider_cooldown_delay=%.3fs "
                "provider_retry_after=%s error_category=%s "
                "provider_call_attempted=true error=%s",
                cycle_id,
                cycle.get("conversation_id"),
                transient_attempt,
                cycle_retry_delay,
                provider_delay,
                exc.retry_after_seconds,
                exc.category,
                exc,
            )
            return
        attempt = int(cycle.get("attempt_count") or 0) + 1
        if attempt >= self.max_retries:
            await transition_cycle(
                cycle_id,
                "failed",
                fields={
                    "attempt_count": attempt,
                    "error_phase": str(cycle.get("status")),
                    "error_message": sanitized,
                    "completed_at": datetime.now(timezone.utc),
                    "next_attempt_at": None,
                    "lease_owner": None,
                    "lease_expires_at": None,
                },
            )
            await self.redis.rpush(
                self.dead_letter,
                json.dumps({**job, "attempt": attempt}, ensure_ascii=False),
            )
        else:
            not_before = datetime.now(timezone.utc) + timedelta(
                seconds=settings.digisac_history_retry_base_seconds
                * (2 ** (attempt - 1))
            )
            await transition_cycle(
                cycle_id,
                "retryable_failure",
                fields={
                    "attempt_count": attempt,
                    "error_phase": str(cycle.get("status")),
                    "error_message": sanitized,
                    "next_attempt_at": not_before,
                    "enqueued_at": None,
                    "lease_owner": None,
                    "lease_expires_at": None,
                },
            )
        logger.exception(
            "Conversation cycle processing failed: cycle_id=%s "
            "conversation_id=%s attempt=%s",
            cycle_id,
            cycle.get("conversation_id"),
            attempt,
        )

    async def _analyze_with_groq(self, context: str, message_count: int) -> Dict[str, Any]:
        """Analisa a conversa usando GROQ"""
        if not context.strip():
            # Sem conteúdo real para analisar — não vale a pena chamar o modelo.
            return {
                "intent_type": "other",
                "confidence": 0.0,
                "title": "Sem conteúdo para análise",
                "description": "O ciclo não possui conteúdo textual para análise.",
                "message_count": message_count,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }

        prompt = build_prompt(context)

        self._ensure_provider_available()
        try:
            logger.info(f"📤 Chamando Groq API com {message_count} mensagens")

            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt()},
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

            result = parse_result(result_text)

            result["message_count"] = message_count
            result["processed_at"] = datetime.now(timezone.utc).isoformat()
            return result

        except Exception as e:
            transient = transient_provider_error(
                e, "Groq classification request"
            )
            if transient is not None:
                logger.warning("Transient Groq classification error: %s", e)
                raise transient from e
            logger.error(f"❌ Erro na API Groq: {e}")
            raise


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
