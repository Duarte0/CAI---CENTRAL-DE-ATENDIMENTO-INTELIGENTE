"""Redis worker that extracts visible information from DigiSac images."""

import asyncio
import base64
import json
import logging
import re
import time
from typing import Any, cast

import requests
from groq import Groq

from src.core.config import settings
from src.core.db import close_database, initialize_database, set_image_extraction_status
from src.core.redis_client import AsyncRedis, create_redis_client

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


class TransientImageExtractionError(RuntimeError):
    """A network/upstream failure worth retrying."""


def _raise_for_status(response: requests.Response, operation: str) -> None:
    if response.status_code in TRANSIENT_HTTP_STATUSES:
        raise TransientImageExtractionError(
            f"{operation} returned transient HTTP {response.status_code}"
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
                f"Groq vision request failed: {exc}"
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
        self.max_retries = settings.max_retry_attempts

    async def process_job(self, job: dict[str, Any]) -> None:
        message_id = job.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("Image job missing message_id")
        await set_image_extraction_status(
            message_id, "processing", increment_attempt=True
        )
        try:
            text = await asyncio.to_thread(extract_image_message, message_id)
        except Exception as exc:
            attempt = int(job.get("attempt", 0)) + 1
            transient = isinstance(exc, TransientImageExtractionError)
            if transient and attempt < self.max_retries:
                delay = min(2**attempt, 60)
                job.update(attempt=attempt, not_before=time.time() + delay)
                await set_image_extraction_status(
                    message_id, "pending", error_message=str(exc)
                )
                await self.redis.rpush(self.queue, json.dumps(job))
                logger.warning(
                    "Image extraction retry scheduled: message_id=%s "
                    "attempt=%s delay=%ss error=%s",
                    message_id,
                    attempt,
                    delay,
                    exc,
                )
                return
            await set_image_extraction_status(
                message_id, "failed", error_message=str(exc)
            )
            await self.redis.rpush(self.dead_letter, json.dumps(job))
            logger.exception(
                "Image extraction failed: message_id=%s attempt=%s",
                message_id,
                attempt,
            )
            return

        await set_image_extraction_status(message_id, "completed", text=text)
        logger.info("Image extraction completed: message_id=%s", message_id)

    async def process(self) -> None:
        logger.info("Image extraction worker started")
        while True:
            try:
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
