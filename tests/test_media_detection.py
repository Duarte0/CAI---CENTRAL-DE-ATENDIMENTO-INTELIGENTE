from datetime import datetime, timezone

import pytest

from src.api import routes
from src.api.webhook_adapter import DigisacWebhookAdapter
from src.api.webhook_adapter import DigisacMessage
from src.core.finalization import apply_media_states, normalize_history, render_context
from src.core.media import is_image_message


IMAGE_DOCUMENT_FILE = {
    "name": "Comprovante-de-Endereco-Luan.jpg",
    "publicFilename": "Comprovante-de-Endereco-Luan.jpg",
    "extension": "jpeg",
    "mimetype": "image/jpeg",
}


def test_image_document_is_detected_by_mimetype_but_pdf_is_not():
    assert is_image_message("document", IMAGE_DOCUMENT_FILE)
    assert not is_image_message(
        "document", {"name": "CNH-LUAN.pdf", "mimetype": "application/pdf"}
    )


def test_webhook_adapter_normalizes_image_document_to_image():
    result = DigisacWebhookAdapter.adapt(
        {
            "event": "message.created",
            "data": {
                "id": "image-document-id",
                "ticketId": "ticket-id",
                "type": "document",
                "isFromMe": False,
                "file": IMAGE_DOCUMENT_FILE,
            },
        }
    )

    assert result.message is not None
    assert result.message.message_type == "image"
    assert result.message.file["mimetype"] == "image/jpeg"


def test_webhook_adapter_keeps_pdf_document_as_document():
    result = DigisacWebhookAdapter.adapt(
        {
            "event": "message.created",
            "data": {
                "id": "pdf-document-id",
                "ticketId": "ticket-id",
                "type": "document",
                "isFromMe": False,
                "file": {
                    "name": "CNH-LUAN.pdf",
                    "mimetype": "application/pdf",
                },
            },
        }
    )

    assert result.message is not None
    assert result.message.message_type == "document"


@pytest.mark.asyncio
async def test_enqueue_image_extraction_accepts_image_document(monkeypatch):
    reserved: list[tuple[str, str, str]] = []

    async def fake_reserve(message_id: str, conversation_id: str, model: str):
        reserved.append((message_id, conversation_id, model))
        return True

    class Redis:
        def __init__(self):
            self.jobs: list[tuple[str, str]] = []

        async def rpush(self, queue: str, job: str):
            self.jobs.append((queue, job))

    monkeypatch.setattr(routes, "reserve_image_extraction", fake_reserve)
    redis = Redis()
    message = DigisacMessage(
        ticketId="ticket-id",
        id="image-document-id",
        type="document",
        isFromMe=False,
        file={"name": "proof.jpg", "mimetype": "image/jpeg"},
    )

    assert await routes.enqueue_image_extraction(redis, message) is True
    assert reserved and reserved[0][:2] == ("image-document-id", "ticket-id")
    assert redis.jobs[0][0] == "image_extraction_queue"


def test_history_normalizes_image_document_and_blocks_failed_extraction():
    closed_at = datetime(2026, 8, 4, 14, 0, tzinfo=timezone.utc)
    normalized = normalize_history(
        [
            {
                "id": "image-document-id",
                "ticketId": "ticket-id",
                "type": "document",
                "timestamp": "2026-08-04T13:18:43+00:00",
                "isFromMe": False,
                "visible": True,
                "file": IMAGE_DOCUMENT_FILE,
            }
        ],
        cycle_started_at=datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc),
        previous_closed_at=None,
        ticket_closed_at=closed_at,
    )

    assert normalized.messages[0]["type"] == "image"
    hydrated, warnings, pending, blocked = apply_media_states(
        normalized.messages,
        {
            "image-document-id": {
                "status": "failed",
                "attempt_count": 3,
            }
        },
        max_attempts=3,
    )
    assert hydrated[0]["media_status"] == "failed"
    assert warnings == []
    assert pending == set()
    assert blocked == {"image-document-id"}


def test_history_context_labels_image_document_as_analyzed_image():
    context = render_context(
        ticket_id="ticket-id",
        protocol="123",
        departments=[],
        agents=[],
        include_administrative_names=False,
        messages=[
            {
                "message_id": "image-document-id",
                "timestamp": "2026-08-04T13:18:43+00:00",
                "sender_type": "client",
                "sender_name": None,
                "type": "document",
                "content": "Endereço residencial confirmado.",
                "file": IMAGE_DOCUMENT_FILE,
            }
        ],
    )

    assert "CLIENTE — IMAGEM ANALISADA" in context
    assert "Endereço residencial confirmado." in context
    assert "DOCUMENTO" not in context
