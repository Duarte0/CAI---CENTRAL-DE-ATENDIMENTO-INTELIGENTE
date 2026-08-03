"""Redis worker that downloads DigiSac voice messages and transcribes them."""

import asyncio
import json
import logging
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
    initialize_database,
    recover_stale_transcriptions,
    release_transcription_publication,
    set_transcription_status,
)
from src.core.redis_client import AsyncRedis, create_redis_client

logger = logging.getLogger(__name__)
GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class TransientTranscriptionError(RuntimeError):
    """A network/upstream failure worth retrying."""


def _raise_for_status(response: requests.Response, operation: str) -> None:
    if response.status_code in TRANSIENT_HTTP_STATUSES:
        raise TransientTranscriptionError(
            f"{operation} returned transient HTTP {response.status_code}"
        )
    response.raise_for_status()


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
        raise TransientTranscriptionError(
            f"DigiSac download failed: {exc}") from exc

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
            raise TransientTranscriptionError(
                f"Groq request failed: {exc}") from exc

        text = response.json().get("text")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Groq returned an empty transcription")
        return text.strip()


class AudioTranscriptionWorker:
    def __init__(self, redis_client: AsyncRedis) -> None:
        self.redis = redis_client
        self.queue = "audio_transcription_queue"
        self.dead_letter = "audio_transcription_dead_letter"
        self.max_retries = settings.max_retry_attempts

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
        except Exception as exc:
            attempt = int(job.get("attempt", 0)) + 1
            transient = isinstance(exc, TransientTranscriptionError)
            if transient and attempt < self.max_retries:
                delay = min(2**attempt, 60)
                job.update(attempt=attempt, not_before=time.time() + delay)
                transitioned = await set_transcription_status(
                    message_id,
                    "pending",
                    error_message=str(exc),
                    next_attempt_at=datetime.fromtimestamp(
                        float(job["not_before"]), tz=timezone.utc
                    ),
                    expected_updated_at=lease,
                )
                if transitioned is None:
                    logger.info(
                        "Discarding stale audio retry result: message_id=%s",
                        message_id,
                    )
                    return
                logger.warning(
                    "Audio transcription retry scheduled: message_id=%s attempt=%s delay=%ss error=%s",
                    message_id,
                    attempt,
                    delay,
                    exc,
                )
                return
            transitioned = await set_transcription_status(
                message_id,
                "failed",
                error_message=str(exc),
                expected_updated_at=lease,
            )
            if transitioned is not None:
                await self.redis.rpush(self.dead_letter, json.dumps(job))
            logger.exception(
                "Audio transcription failed: message_id=%s attempt=%s",
                message_id,
                attempt,
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
        logger.info("Audio transcription completed: message_id=%s", message_id)

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
            except Exception as exc:
                await release_transcription_publication(
                    message_id,
                    f"recovery queue publish failed: {exc}",
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
        while True:
            try:
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
