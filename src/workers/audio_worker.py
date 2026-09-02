"""Redis worker that downloads DigiSac voice messages and transcribes them."""

import asyncio
import logging
import re
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import requests

from src.core.config import settings
from src.core.db import (
    claim_next_transcription,
    close_database,
    initialize_database,
    set_transcription_status,
)
from src.core.provider_retry import (
    TransientProviderError,
    retry_after_from_text,
    retry_after_seconds,
    retry_delay,
)

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


def _is_transient_failure_text(  # pyright: ignore[reportUnusedFunction]
    message: str | None,
) -> bool:
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
    def __init__(self, owner: str | None = None) -> None:
        self.owner = owner or f"audio-worker:{uuid.uuid4()}"
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
        """Compatibility hook that claims one specific durable message."""
        message_id = job.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("Audio job missing message_id")
        claim = await claim_next_transcription(
            owner=self.owner,
            lease_seconds=settings.content_recovery_lease_seconds,
            message_id=message_id,
        )
        if claim is None:
            logger.info(
                "Skipping duplicate, scheduled or unavailable audio row: "
                "message_id=%s",
                message_id,
            )
            return
        await self._process_claim(claim)

    async def _process_claim(self, claim: dict[str, Any]) -> None:
        message_id = str(claim["message_id"])
        lease = claim.get("updated_at")
        if isinstance(lease, str):
            lease = datetime.fromisoformat(lease.replace("Z", "+00:00"))
        if not isinstance(lease, datetime):
            raise RuntimeError("Audio claim did not contain an updated_at token")
        attempt = int(claim.get("attempt_count") or 0)
        try:
            text = await asyncio.to_thread(transcribe_message, message_id)
            if not text.strip():
                raise RuntimeError("empty transcription")
            text = text.strip()
        except Exception as exc:
            transient = isinstance(exc, TransientTranscriptionError)
            safe_error = _safe_audio_error(exc)
            if transient:
                delay = self._retry_delay(exc, attempt)
                retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                self.rate_limited_until = max(
                    self.rate_limited_until, retry_at.timestamp()
                )
                transitioned = await set_transcription_status(
                    message_id,
                    "pending",
                    error_message=safe_error,
                    next_attempt_at=retry_at,
                    expected_updated_at=lease,
                    expected_lease_owner=self.owner,
                )
                if transitioned is None:
                    logger.info(
                        "Discarding stale audio retry result: message_id=%s",
                        message_id,
                    )
                    return
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
            transitioned = await set_transcription_status(
                message_id,
                "failed",
                error_message=safe_error,
                expected_updated_at=lease,
                expected_lease_owner=self.owner,
            )
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
            expected_lease_owner=self.owner,
        )
        if transitioned is None:
            logger.info(
                "Discarding stale audio completion: message_id=%s",
                message_id,
            )
            return
        logger.info("Audio transcription completed: message_id=%s", message_id)

    async def poll_once(self) -> bool:
        """Claim and process one due row, returning whether work was found."""
        if self.rate_limited_until > time.time():
            return False
        claim = await claim_next_transcription(
            owner=self.owner,
            lease_seconds=settings.content_recovery_lease_seconds,
        )
        if claim is None:
            return False
        await self._process_claim(claim)
        return True

    async def process(self) -> None:
        logger.info("Audio transcription worker started")
        while True:
            try:
                rate_limit_remaining = self.rate_limited_until - time.time()
                if rate_limit_remaining > 0:
                    await asyncio.sleep(min(rate_limit_remaining, 1.0))
                    continue
                processed = await self.poll_once()
                if not processed:
                    await asyncio.sleep(settings.content_extraction_poll_seconds)
                    continue
            except Exception:
                logger.exception("Unexpected audio worker loop failure")
                await asyncio.sleep(1)


async def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    await initialize_database()
    try:
        await AudioTranscriptionWorker().process()
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
