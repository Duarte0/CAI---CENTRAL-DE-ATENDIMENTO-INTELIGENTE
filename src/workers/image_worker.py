"""Redis worker that extracts visible information from DigiSac images."""

import asyncio
import base64
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, cast

import requests
from groq import Groq

from src.core.config import settings
from src.core.db import (
    close_database,
    get_image_extraction,
    initialize_database,
    recover_stale_image_extractions,
    release_image_publication,
    reserve_image_extraction,
    set_image_extraction_status,
)
from src.core.redis_client import AsyncRedis, create_redis_client
from src.core.provider_retry import (
    TransientProviderError,
    retry_after_from_text,
    retry_after_seconds,
    retry_delay,
)

logger = logging.getLogger(__name__)
TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
VISION_PROMPT = (
    "Você trabalha dando suporte a um escritório de contabilidade que atende "
    "clientes via WhatsApp. Olhe a imagem enviada nesta conversa e escreva um "
    "resumo curto (2-4 frases) explicando o que ela mostra e por que isso "
    "pode ser relevante para o atendimento contábil — por exemplo: um "
    "documento fiscal, uma guia de imposto, um comprovante, uma print de erro "
    "de sistema, uma foto de produto/mercadoria, ou uma conversa.\n\n"
    "Inclua no resumo os dados concretos que aparecerem (empresa, CNPJ, "
    "valores, datas, vencimentos, protocolos, status), mas escreva como um "
    "resumo corrido, não como uma lista de campos. Não descreva elementos de "
    "interface do celular ou do aplicativo (hora, bateria, ícones, botões), "
    "a menos que sejam o próprio assunto da imagem. Não invente informações. "
    "Se a imagem não tiver relevância para o atendimento, diga isso em uma frase."
)


class TransientImageExtractionError(TransientProviderError):
    """A network/upstream failure worth retrying."""

    def __init__(
        self, message: str, *, retry_after_seconds: float | None = None
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _retry_after_from_text(message: str) -> float | None:
    return retry_after_from_text(message)


def _retry_after_seconds(exc: BaseException) -> float | None:
    return retry_after_seconds(exc)


def _is_transient_failure_text(message: str | None) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return bool(
        re.search(r"(?:error code:|http)\s*(?:408|425|429|500|502|503|504)", lowered)
        or "rate_limit_exceeded" in lowered
        or "tokens per minute" in lowered
        or "tokens per day" in lowered
        or "currently over capacity" in lowered
        or "apiconnectionerror" in lowered
        or "apitimeouterror" in lowered
        or "transient http" in lowered
    )


def _retry_delay(exc: TransientImageExtractionError, attempt: int) -> float:
    return retry_delay(
        attempt=attempt,
        base_seconds=settings.image_retry_base_seconds,
        max_delay_seconds=settings.image_retry_max_delay_seconds,
        provider_margin_seconds=settings.image_retry_provider_margin_seconds,
        provider_delay_seconds=(
            exc.retry_after_seconds
            if exc.retry_after_seconds is not None
            else _retry_after_from_text(str(exc))
        ),
    )


def _raise_for_status(response: requests.Response, operation: str) -> None:
    if response.status_code in TRANSIENT_HTTP_STATUSES:
        raise TransientImageExtractionError(
            f"{operation} returned transient HTTP {response.status_code}",
            retry_after_seconds=_retry_after_seconds(
                requests.HTTPError(response=response)
            ),
        )
    response.raise_for_status()


def _file_info(payload: Any) -> dict[str, Any] | None:
    """Find the DigiSac file object without relying on one envelope shape."""
    if isinstance(payload, dict):
        payload = cast(dict[str, Any], payload)
        file_value = payload.get("file")
        if isinstance(file_value, dict):
            return cast(dict[str, Any], file_value)
        for value in payload.values():
            found = _file_info(value)
            if found:
                return found
    elif isinstance(payload, list):
        payload = cast(list[Any], payload)
        for value in payload:
            found = _file_info(value)
            if found:
                return found
    return None


def extract_image_message(
    message_id: str, *, client: Groq | None = None
) -> str:
    """Execute the blocking DigiSac download and Groq vision pipeline."""
    if not settings.digisac_api_key:
        raise RuntimeError(
            "DIGISAC_API_KEY is required to run the image worker")
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is required to run the image worker")

    message_url = f"{settings.digisac_api_base_url.rstrip('/')}/messages/{message_id}"
    completion: Any | None = None
    try:
        metadata = requests.get(
            message_url,
            params={"include[0]": "file"},
            headers={"Authorization": f"Bearer {settings.digisac_api_key}"},
            timeout=settings.image_download_timeout_seconds,
        )
        _raise_for_status(metadata, "DigiSac message lookup")
        file_info = _file_info(metadata.json())
        if not file_info:
            raise RuntimeError(
                "DigiSac response does not contain file metadata")
        mimetype = file_info.get("mimetype")
        if not isinstance(mimetype, str) or not mimetype.lower().startswith("image/"):
            raise RuntimeError(
                f"DigiSac file is not an image: mimetype={mimetype!r}")
        image_url = file_info.get("url")
        if not isinstance(image_url, str) or not image_url.startswith(
            ("http://", "https://")
        ):
            raise RuntimeError(
                "DigiSac response does not contain an image URL")

        image = requests.get(
            image_url,
            timeout=settings.image_download_timeout_seconds,
        )
        _raise_for_status(image, "image download")
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise TransientImageExtractionError(
            f"DigiSac image download failed: {exc}"
        ) from exc

    content_length = image.headers.get("content-length")
    if content_length and int(content_length) > settings.image_max_bytes:
        raise RuntimeError(
            f"Image exceeds {settings.image_max_bytes} byte processing limit"
        )
    if len(image.content) > settings.image_max_bytes:
        raise RuntimeError(
            f"Image exceeds {settings.image_max_bytes} byte processing limit"
        )
    if not image.content:
        raise RuntimeError("DigiSac returned an empty image")

    data_uri = (
        f"data:{mimetype};base64,"
        + base64.b64encode(image.content).decode("ascii")
    )
    # Retry decisions belong to this worker. The SDK's implicit retries can wait
    # and spend the whole job retry budget without adapting the requested TPM.
    groq_client = client or Groq(api_key=settings.groq_api_key, max_retries=0)
    completion_tokens = settings.image_vision_max_completion_tokens

    def create_completion(max_completion_tokens: int):
        return groq_client.chat.completions.create(
            model=settings.image_vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_uri},
                        },
                    ],
                }
            ],
            max_completion_tokens=max_completion_tokens,
            reasoning_format="hidden",
            timeout=settings.image_extraction_timeout_seconds,
        )

    try:
        completion = create_completion(completion_tokens)
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        token_limit = re.search(
            r"tokens per minute \(TPM\): Limit (\d+), Requested (\d+)",
            str(exc),
        )
        if status_code == 413 and token_limit:
            limit, requested = map(int, token_limit.groups())
            adjusted_completion_tokens = completion_tokens - \
                (requested - limit)
            if adjusted_completion_tokens < 1:
                raise RuntimeError(
                    "Groq vision input alone exceeds the TPM token budget"
                ) from exc
            logger.warning(
                "Reducing Groq vision completion budget after TPM 413: "
                "configured=%s requested_total=%s limit=%s adjusted=%s",
                completion_tokens,
                requested,
                limit,
                adjusted_completion_tokens,
            )
            try:
                completion = create_completion(adjusted_completion_tokens)
            except Exception as retry_exc:
                exc = retry_exc
                status_code = getattr(retry_exc, "status_code", None)
                current_tpm = re.search(
                    r"Limit (\d+), Used (\d+), Requested (\d+)",
                    str(retry_exc),
                )
                if status_code == 429 and current_tpm:
                    limit, used, retry_requested = map(
                        int, current_tpm.groups())
                    # Leave a small margin because other workers may consume
                    # tokens between the rate-limit response and this retry.
                    tpm_headroom = 100
                    available = max(limit - used - tpm_headroom, 0)
                    second_adjustment = retry_requested - available
                    available_completion_tokens = (
                        adjusted_completion_tokens - second_adjustment
                    )
                    if available_completion_tokens >= 256:
                        logger.warning(
                            "Reducing Groq vision budget to current TPM availability: "
                            "limit=%s used=%s requested=%s adjusted=%s",
                            limit,
                            used,
                            retry_requested,
                            available_completion_tokens,
                        )
                        try:
                            completion = create_completion(
                                available_completion_tokens
                            )
                        except Exception as final_exc:
                            exc = final_exc
                            status_code = getattr(
                                final_exc, "status_code", None)
                        else:
                            exc = None
            else:
                exc = None
        if exc is None:
            pass
        elif (
            status_code in TRANSIENT_HTTP_STATUSES
            or type(exc).__name__
            in {"APIConnectionError", "APITimeoutError", "RateLimitError"}
        ):
            raise TransientImageExtractionError(
                f"Groq vision request failed: {exc}",
                retry_after_seconds=_retry_after_seconds(exc),
            ) from exc
        else:
            raise exc

    if completion is None:
        raise RuntimeError("Groq vision request did not produce a completion")
    choice = completion.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError(
            "Groq vision response exceeded the output token limit")
    text = choice.message.content
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Groq returned an empty image extraction")
    return text.strip()


class ImageExtractionWorker:
    def __init__(self, redis_client: AsyncRedis) -> None:
        self.redis = redis_client
        self.queue = "image_extraction_queue"
        self.dead_letter = "image_extraction_dead_letter"
        self.rate_limited_until = 0.0

    async def process_job(self, job: dict[str, Any]) -> None:
        message_id = job.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("Image job missing message_id")
        lease = await set_image_extraction_status(
            message_id, "processing", increment_attempt=True
        )
        if lease is None:
            logger.info(
                "Skipping duplicate or stale image job: message_id=%s",
                message_id,
            )
            return
        await self._remove_matching_queue_items(message_id)
        try:
            text = await asyncio.to_thread(extract_image_message, message_id)
        except Exception as exc:
            attempt = int(job.get("attempt", 0)) + 1
            transient = isinstance(exc, TransientImageExtractionError)
            if transient:
                delay = _retry_delay(exc, attempt)
                retry_at = time.time() + delay
                self.rate_limited_until = max(
                    self.rate_limited_until, retry_at
                )
                job.update(attempt=attempt, not_before=retry_at)
                transitioned = await set_image_extraction_status(
                    message_id,
                    "pending",
                    error_message=str(exc),
                    next_attempt_at=datetime.fromtimestamp(
                        retry_at, tz=timezone.utc
                    ),
                    expected_updated_at=lease,
                )
                if transitioned is None:
                    logger.info(
                        "Discarding stale image retry result: message_id=%s",
                        message_id,
                    )
                    return
                logger.warning(
                    "Image extraction retry scheduled: message_id=%s "
                    "attempt=%s delay=%.3fs provider_retry_after=%s error=%s",
                    message_id,
                    attempt,
                    delay,
                    exc.retry_after_seconds,
                    exc,
                )
                return
            transitioned = await set_image_extraction_status(
                message_id,
                "failed",
                error_message=str(exc),
                expected_updated_at=lease,
            )
            if transitioned is not None:
                await self.redis.rpush(
                    self.dead_letter,
                    json.dumps({**job, "attempt": attempt}),
                )
            logger.exception(
                "Image extraction failed: message_id=%s attempt=%s",
                message_id,
                attempt,
            )
            return

        transitioned = await set_image_extraction_status(
            message_id,
            "completed",
            text=text,
            expected_updated_at=lease,
        )
        if transitioned is None:
            logger.info(
                "Discarding stale image completion: message_id=%s",
                message_id,
            )
            return
        removed = await self._remove_matching_dead_letters(message_id)
        if removed:
            logger.info(
                "Removed stale image dead letters after success: "
                "message_id=%s count=%s",
                message_id,
                removed,
            )
        logger.info("Image extraction completed: message_id=%s", message_id)

    async def _remove_matching_dead_letters(self, message_id: str) -> int:
        removed = 0
        raw_items = await self.redis.lrange(self.dead_letter, 0, -1)
        for raw in dict.fromkeys(raw_items):
            try:
                parsed: Any = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                job = cast(dict[str, Any], parsed)
                if job.get("message_id") == message_id:
                    removed += await self.redis.lrem(self.dead_letter, 0, raw)
        return removed

    async def _remove_matching_queue_items(self, message_id: str) -> int:
        removed = 0
        raw_items = await self.redis.lrange(self.queue, 0, -1)
        for raw in dict.fromkeys(raw_items):
            try:
                parsed: Any = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                queued_job = cast(dict[str, Any], parsed)
                if queued_job.get("message_id") == message_id:
                    removed += await self.redis.lrem(self.queue, 0, raw)
        if removed:
            logger.info(
                "Removed duplicate image queue items: message_id=%s count=%s",
                message_id,
                removed,
            )
        return removed

    async def recover_transient_dead_letters(self) -> int:
        """Republish legacy transient failures without deleting their safety copy."""
        raw_items = await self.redis.lrange(self.dead_letter, 0, -1)
        jobs_by_message: dict[str, tuple[str, dict[str, Any]]] = {}
        for raw in raw_items:
            try:
                parsed: Any = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(parsed, dict):
                continue
            job = cast(dict[str, Any], parsed)
            message_id = job.get("message_id")
            if isinstance(message_id, str) and message_id:
                jobs_by_message.setdefault(message_id, (raw, job))

        published = 0
        for message_id, (_raw, job) in jobs_by_message.items():
            row = await get_image_extraction(message_id)
            if row is None:
                continue
            if row["status"] == "completed":
                await self._remove_matching_dead_letters(message_id)
                continue
            error_message = row.get("error_message")
            if row["status"] != "failed" or not _is_transient_failure_text(
                error_message if isinstance(error_message, str) else None
            ):
                continue
            reserved = await reserve_image_extraction(
                message_id,
                row.get("conversation_id"),
                str(row.get("model") or settings.image_vision_model),
            )
            if not reserved:
                continue
            attempt = max(int(job.get("attempt", 0)), 0)
            retry_error = TransientImageExtractionError(
                "legacy transient image dead letter",
                # A stored provider delay is relative to the original failure
                # and is stale by the time the reconciler sees it.
                retry_after_seconds=0.0,
            )
            delay = _retry_delay(retry_error, attempt + 1)
            retry_at = time.time() + delay
            scheduled_at = datetime.fromtimestamp(
                retry_at, tz=timezone.utc
            )
            transitioned = await set_image_extraction_status(
                message_id,
                "pending",
                error_message=str(retry_error),
                next_attempt_at=scheduled_at,
                expected_statuses=("pending",),
            )
            if transitioned is None:
                continue
            published += 1
            logger.warning(
                "Recovered transient image dead letter: message_id=%s "
                "attempt=%s delay=%.3fs",
                message_id,
                attempt,
                delay,
            )
        return published

    async def recover_stale_jobs(self) -> int:
        rows = await recover_stale_image_extractions(
            lease_seconds=settings.content_recovery_lease_seconds,
            batch_size=settings.content_recovery_batch_size,
        )
        queued_message_ids: set[str] = set()
        for raw in await self.redis.lrange(self.queue, 0, -1):
            try:
                parsed: Any = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                queued_job = cast(dict[str, Any], parsed)
                queued_id = queued_job.get("message_id")
                if isinstance(queued_id, str) and queued_id:
                    queued_message_ids.add(queued_id)
        published = 0
        for row in rows:
            message_id = str(row["message_id"])
            if message_id in queued_message_ids:
                logger.debug(
                    "Image publication already present in Redis: message_id=%s",
                    message_id,
                )
                continue
            attempt_count = int(row["attempt_count"])
            job = {
                "message_id": message_id,
                "conversation_id": row["conversation_id"],
                "attempt": max(attempt_count, 0),
            }
            try:
                await self.redis.rpush(self.queue, json.dumps(job))
            except Exception as exc:
                await release_image_publication(
                    message_id,
                    f"recovery queue publish failed: {exc}",
                )
                raise
            published += 1
            queued_message_ids.add(message_id)
        if published:
            logger.warning("Recovered stale image jobs: count=%s", published)
        return published

    async def process(self) -> None:
        logger.info("Image extraction worker started")
        next_recovery_at = 0.0
        next_dead_letter_recovery_at = 0.0
        while True:
            try:
                rate_limit_remaining = self.rate_limited_until - time.time()
                if rate_limit_remaining > 0:
                    await asyncio.sleep(min(rate_limit_remaining, 1.0))
                    continue
                if time.monotonic() >= next_dead_letter_recovery_at:
                    await self.recover_transient_dead_letters()
                    next_dead_letter_recovery_at = (
                        time.monotonic()
                        + settings.image_dead_letter_recovery_interval_seconds
                    )
                if time.monotonic() >= next_recovery_at:
                    await self.recover_stale_jobs()
                    next_recovery_at = (
                        time.monotonic()
                        + settings.content_reconcile_interval_seconds
                    )
                raw = await self.redis.lpop(self.queue)
                if not raw:
                    await asyncio.sleep(1)
                    continue
                parsed_job: Any = json.loads(raw)
                if not isinstance(parsed_job, dict):
                    raise ValueError("Image queue item must be a JSON object")
                job = cast(dict[str, Any], parsed_job)
                not_before = float(job.get("not_before", 0))
                if not_before > time.time():
                    await self.redis.rpush(self.queue, raw)
                    await asyncio.sleep(min(not_before - time.time(), 1))
                    continue
                await self.process_job(job)
            except Exception:
                logger.exception("Unexpected image worker loop failure")
                await asyncio.sleep(1)


async def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    await initialize_database()
    redis_client = create_redis_client()
    try:
        await ImageExtractionWorker(redis_client).process()
    finally:
        await redis_client.aclose()
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
