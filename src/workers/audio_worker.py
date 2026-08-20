"""Redis worker that downloads DigiSac voice messages and transcribes them."""

import asyncio
import json
import logging
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import requests

from src.core.config import settings
from src.core.db import (
    close_database,
    get_transcription,
    initialize_database,
    recover_stale_transcriptions,
    release_transcription_publication,
    set_transcription_status,
)
from src.core.provider_retry import (
    TransientProviderError,
    retry_after_from_text,
    retry_after_seconds,
    retry_delay,
)
from src.core.redis_client import AsyncRedis, create_redis_client

logger = logging.getLogger(__name__)
GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class TransientTranscriptionError(TransientProviderError):
    """A network/upstream failure worth retrying."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        category: str = "transient_provider",
    ) -> None:
        super().__init__(
            message,
            retry_after_seconds=retry_after_seconds,
            category=category,
        )


def _response_retry_after(response: requests.Response) -> float | None:
    header_delay = retry_after_seconds(
        requests.HTTPError(response=response)
    )
    if header_delay is not None:
        return header_delay
    try:
        return retry_after_from_text(getattr(response, "text", ""))
    except Exception:
        return None


def _raise_for_status(response: requests.Response, operation: str) -> None:
    if response.status_code in TRANSIENT_HTTP_STATUSES:
        category = {
            408: "http_408",
            425: "http_425",
            429: "http_429",
            500: "http_500",
            502: "http_502",
            503: "http_503",
            504: "http_504",
        }[response.status_code]
        raise TransientTranscriptionError(
            f"{operation} returned transient HTTP {response.status_code}",
            retry_after_seconds=_response_retry_after(response),
            category=category,
        )
    response.raise_for_status()


def _safe_audio_error(exc: BaseException) -> str:
    if isinstance(exc, TransientTranscriptionError):
        return f"transient_audio_failure:{exc.category}"
    if isinstance(exc, subprocess.TimeoutExpired):
        return "audio_conversion_timeout"
    if isinstance(exc, RuntimeError):
        lowered = str(exc).lower()
        if "does not contain" in lowered:
            return "audio_file_missing"
        if "ffmpeg conversion" in lowered:
            return "audio_conversion_failed"
        if "empty transcription" in lowered:
            return "audio_transcription_empty"
    return "audio_transcription_failed"


def _is_transient_failure_text(message: str | None) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return bool(
        "transient_audio_failure:" in lowered
        or re.search(
            r"(?:error code:|http|transient http)\s*(?:408|425|429|500|502|503|504)",
            lowered,
        )
        or "rate_limit_exceeded" in lowered
        or "tokens per minute" in lowered
        or "tokens per day" in lowered
        or "currently over capacity" in lowered
        or "apiconnectionerror" in lowered
        or "apitimeouterror" in lowered
        or "timeout" in lowered
        or "connection" in lowered
    )


def _find_audio_url(payload: Any) -> str | None:
    """Find common DigiSac file URL fields without depending on one envelope shape."""
    if isinstance(payload, dict):
        payload = cast(dict[str, Any], payload)
        for key in ("url", "publicUrl", "downloadUrl", "fileUrl"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        for value in payload.values():
            found = _find_audio_url(value)
            if found:
                return found
    elif isinstance(payload, list):
        payload = cast(list[Any], payload)
        for value in payload:
            found = _find_audio_url(value)
            if found:
                return found
    return None


def transcribe_message(message_id: str) -> str:
    """Execute the blocking DigiSac/download/ffmpeg/Groq pipeline."""
    if not settings.digisac_api_key:
        raise RuntimeError(
            "DIGISAC_API_KEY is required to run the audio worker")
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is required to run the audio worker")

    message_url = f"{settings.digisac_api_base_url.rstrip('/')}/messages/{message_id}"
    try:
        metadata = requests.get(
            message_url,
            params={"include[0]": "file"},
            headers={"Authorization": f"Bearer {settings.digisac_api_key}"},
            timeout=settings.audio_download_timeout_seconds,
        )
        _raise_for_status(metadata, "DigiSac message lookup")
        audio_url = _find_audio_url(metadata.json())
        if not audio_url:
            raise RuntimeError(
                "DigiSac response does not contain an audio file URL")

        audio = requests.get(
            audio_url, timeout=settings.audio_download_timeout_seconds)
        _raise_for_status(audio, "audio download")
    except (requests.Timeout, requests.ConnectionError) as exc:
        category = "timeout" if isinstance(exc, requests.Timeout) else "connection"
        raise TransientTranscriptionError(
            f"DigiSac download failed ({category})",
            retry_after_seconds=retry_after_seconds(exc),
            category=category,
        ) from exc

    with tempfile.TemporaryDirectory(prefix="cai-audio-") as temp_dir:
        source = Path(temp_dir) / "recording.oga"
        wav = Path(temp_dir) / "recording.wav"
        source.write_bytes(audio.content)
        try:
            conversion = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-y",
                    "-i",
                    str(source),
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "16000",
                    str(wav),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=settings.audio_conversion_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ffmpeg conversion timed out") from exc
        if conversion.returncode != 0:
            error = conversion.stderr.strip()[-1000:]
            raise RuntimeError(f"ffmpeg conversion failed: {error}")

        try:
            with wav.open("rb") as wav_file:
                response = requests.post(
                    GROQ_TRANSCRIPTIONS_URL,
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                    files={"file": ("recording.wav", wav_file, "audio/wav")},
                    data={
                        "model": settings.audio_transcription_model,
                        "language": "pt",
                        "response_format": "json",
                    },
                    timeout=settings.audio_transcription_timeout_seconds,
                )
            _raise_for_status(response, "Groq transcription")
        except (requests.Timeout, requests.ConnectionError) as exc:
            category = "timeout" if isinstance(exc, requests.Timeout) else "connection"
            raise TransientTranscriptionError(
                f"Groq request failed ({category})",
                retry_after_seconds=retry_after_seconds(exc),
                category=category,
            ) from exc

        text = response.json().get("text")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Groq returned an empty transcription")
        return text.strip()


class AudioTranscriptionWorker:
    def __init__(self, redis_client: AsyncRedis) -> None:
        self.redis = redis_client
        self.queue = "audio_transcription_queue"
        self.dead_letter = "audio_transcription_dead_letter"
        self.rate_limited_until = 0.0

    def _retry_delay(self, exc: TransientTranscriptionError, attempt: int) -> float:
        return retry_delay(
            attempt=attempt,
            base_seconds=settings.audio_retry_base_seconds,
            max_delay_seconds=settings.audio_retry_max_delay_seconds,
            provider_margin_seconds=settings.audio_retry_provider_margin_seconds,
            provider_delay_seconds=exc.retry_after_seconds,
        )

    async def process_job(self, job: dict[str, Any]) -> None:
        message_id = job.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("Audio job missing message_id")
        lease = await set_transcription_status(
            message_id, "processing", increment_attempt=True
        )
        if lease is None:
            logger.info(
                "Skipping duplicate or stale audio job: message_id=%s",
                message_id,
            )
            return
        await self._remove_matching_queue_items(message_id)
        try:
            text = await asyncio.to_thread(transcribe_message, message_id)
            if not text.strip():
                raise RuntimeError("empty transcription")
            text = text.strip()
        except Exception as exc:
            attempt = int(job.get("attempt", 0)) + 1
            transient = isinstance(exc, TransientTranscriptionError)
            safe_error = _safe_audio_error(exc)
            if transient:
                delay = self._retry_delay(exc, attempt)
                retry_at = time.time() + delay
                self.rate_limited_until = max(self.rate_limited_until, retry_at)
                job.update(attempt=attempt, not_before=retry_at)
                transitioned = await set_transcription_status(
                    message_id,
                    "pending",
                    error_message=safe_error,
                    next_attempt_at=datetime.fromtimestamp(
                        retry_at, tz=timezone.utc
                    ),
                    expected_updated_at=lease,
                )
                if transitioned is None:
                    logger.info(
                        "Discarding stale audio retry result: message_id=%s",
                        message_id,
                    )
                    return
                try:
                    await self.redis.rpush(self.queue, json.dumps(job))
                except Exception:
                    await release_transcription_publication(
                        message_id,
                        "audio retry queue publication failed",
                    )
                    raise
                logger.warning(
                    "Audio transcription retry scheduled: message_id=%s "
                    "attempt=%s delay=%.3fs provider_retry_after=%s error=%s",
                    message_id,
                    attempt,
                    delay,
                    exc.retry_after_seconds,
                    safe_error,
                )
                return
            job["attempt"] = attempt
            transitioned = await set_transcription_status(
                message_id,
                "failed",
                error_message=safe_error,
                expected_updated_at=lease,
            )
            if transitioned is not None:
                await self._remove_matching_dead_letters(message_id)
                await self.redis.rpush(self.dead_letter, json.dumps(job))
            logger.error(
                "Audio transcription failed: message_id=%s attempt=%s error=%s",
                message_id,
                attempt,
                safe_error,
            )
            return

        transitioned = await set_transcription_status(
            message_id,
            "completed",
            text=text,
            expected_updated_at=lease,
        )
        if transitioned is None:
            logger.info(
                "Discarding stale audio completion: message_id=%s",
                message_id,
            )
            return
        await self._remove_matching_dead_letters(message_id)
        logger.info("Audio transcription completed: message_id=%s", message_id)

    async def _remove_matching_dead_letters(self, message_id: str) -> int:
        removed = 0
        raw_items = await self.redis.lrange(self.dead_letter, 0, -1)
        for raw in dict.fromkeys(raw_items):
            try:
                parsed: Any = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                parsed_job = cast(dict[str, Any], parsed)
            else:
                continue
            if parsed_job.get("message_id") == message_id:
                removed += await self.redis.lrem(self.dead_letter, 0, raw)
        return removed

    async def _deduplicate_message_jobs(self, queue: str) -> set[str]:
        seen: set[str] = set()
        for raw in await self.redis.lrange(queue, 0, -1):
            try:
                parsed: Any = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(parsed, dict):
                continue
            message_id = cast(dict[str, Any], parsed).get("message_id")
            if not isinstance(message_id, str) or not message_id:
                continue
            if message_id in seen:
                await self.redis.lrem(queue, 1, raw)
            else:
                seen.add(message_id)
        return seen

    async def recover_transient_dead_letters(self) -> int:
        await self._deduplicate_message_jobs(self.dead_letter)
        raw_items = await self.redis.lrange(self.dead_letter, 0, -1)
        queued_message_ids = await self._deduplicate_message_jobs(self.queue)
        jobs_by_message: dict[str, dict[str, Any]] = {}
        for raw in raw_items:
            try:
                parsed: Any = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(parsed, dict):
                continue
            parsed_job = cast(dict[str, Any], parsed)
            message_id = parsed_job.get("message_id")
            if isinstance(message_id, str) and message_id:
                jobs_by_message.setdefault(message_id, parsed_job)

        recovered = 0
        for message_id, job in jobs_by_message.items():
            row = await get_transcription(message_id)
            if row is None:
                continue
            if row["status"] == "completed":
                await self._remove_matching_dead_letters(message_id)
                continue
            if message_id in queued_message_ids:
                continue
            error_message = row.get("error_message")
            if row["status"] != "failed" or not isinstance(error_message, str):
                continue
            if not _is_transient_failure_text(error_message):
                continue
            attempt = max(int(job.get("attempt", 0)), 0)
            delay = self._retry_delay(
                TransientTranscriptionError(
                    "legacy transient audio dead letter",
                    retry_after_seconds=0.0,
                ),
                attempt + 1,
            )
            retry_at = time.time() + delay
            transitioned = await set_transcription_status(
                message_id,
                "pending",
                error_message="transient_audio_failure:legacy_dead_letter",
                next_attempt_at=datetime.fromtimestamp(retry_at, tz=timezone.utc),
                expected_statuses=("failed",),
            )
            if transitioned is not None:
                job = {
                    "message_id": message_id,
                    "conversation_id": row.get("conversation_id"),
                    "attempt": attempt,
                    "not_before": retry_at,
                }
                try:
                    await self.redis.rpush(self.queue, json.dumps(job))
                except Exception:
                    await release_transcription_publication(
                        message_id,
                        "audio dead-letter recovery publication failed",
                    )
                    raise
                queued_message_ids.add(message_id)
                recovered += 1
        return recovered

    async def _remove_matching_queue_items(self, message_id: str) -> int:
        removed = 0
        raw_items = await self.redis.lrange(self.queue, 0, -1)
        for raw in dict.fromkeys(raw_items):
            try:
                parsed: Any = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                job = cast(dict[str, Any], parsed)
                if job.get("message_id") == message_id:
                    removed += await self.redis.lrem(self.queue, 0, raw)
        if removed:
            logger.info(
                "Removed duplicate audio queue items: message_id=%s count=%s",
                message_id,
                removed,
            )
        return removed

    async def recover_stale_jobs(self) -> int:
        rows = await recover_stale_transcriptions(
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
                    "Audio publication already present in Redis: message_id=%s",
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
            except Exception:
                await release_transcription_publication(
                    message_id,
                    "audio stale-job recovery publication failed",
                )
                raise
            published += 1
            queued_message_ids.add(message_id)
        if published:
            logger.warning("Recovered stale audio jobs: count=%s", published)
        return published

    async def process(self) -> None:
        logger.info("Audio transcription worker started")
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
                        + settings.audio_dead_letter_recovery_interval_seconds
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
                    raise ValueError("Audio queue item must be a JSON object")
                job = cast(dict[str, Any], parsed_job)
                not_before = float(job.get("not_before", 0))
                if not_before > time.time():
                    await self.redis.rpush(self.queue, raw)
                    await asyncio.sleep(min(not_before - time.time(), 1))
                    continue
                await self.process_job(job)
            except Exception:
                logger.exception("Unexpected audio worker loop failure")
                await asyncio.sleep(1)


async def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    await initialize_database()
    redis_client = create_redis_client()
    try:
        await AudioTranscriptionWorker(redis_client).process()
    finally:
        await redis_client.aclose()
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
