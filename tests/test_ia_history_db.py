import json

import psycopg
import pytest

from src.core.config import settings
from src.core.db import (
    get_completed_image_extractions,
    get_completed_transcriptions,
    get_image_extraction,
    get_pending_content_extractions,
    get_transcription,
    insert_classification,
    reserve_image_extraction,
    reserve_transcription,
    set_image_extraction_status,
    set_transcription_status,
    update_analysis_protocol,
)

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_classification_history_is_persisted_as_jsonb():
    await insert_classification(
        conversation_id="conversation-1",
        message_ids=["message-1", "message-2"],
        created_at="2026-07-20T12:00:00+00:00",
        full_context="Cliente: Preciso emitir uma guia.",
        message_count=2,
        result={
            "intent_type": "question",
            "confidence": 0.91,
            "title": "Emissão de guia",
            "description": "Cliente precisa de uma guia.",
            "department": ["Atendimento"],
            "agent": ["Ana"],
        },
        model="test-model",
        processing_time_ms=42,
        prompt_version="v2",
    )
    with psycopg.connect(settings.database_url) as connection:
        row = connection.execute(
            """
            SELECT conversation_id, message_ids, department, agent,
                   intent_type, confidence, prompt_version, reviewed_at,
                   pg_typeof(message_ids)::text
            FROM ia_classifications
            """
        ).fetchone()
    assert row[:8] == (
        "conversation-1",
        ["message-1", "message-2"],
        ["Atendimento"],
        ["Ana"],
        "question",
        0.91,
        "v2",
        None,
    )
    assert row[8] == "jsonb"


@pytest.mark.asyncio
async def test_transcription_is_idempotently_reserved_and_persisted():
    assert await reserve_transcription("message-1", "ticket-1", "whisper-test")
    assert not await reserve_transcription("message-1", "ticket-1", "whisper-test")
    await set_transcription_status("message-1", "processing", increment_attempt=True)
    await set_transcription_status(
        "message-1", "completed", text="Preciso emitir uma guia."
    )
    row = await get_transcription("message-1")
    assert row is not None
    assert row["conversation_id"] == "ticket-1"
    assert row["text"] == "Preciso emitir uma guia."
    assert row["status"] == "completed"
    assert row["attempt_count"] == 1


@pytest.mark.asyncio
async def test_image_extraction_and_pending_lookup():
    assert await reserve_image_extraction("image-1", "ticket-1", "vision-test")
    assert not await reserve_image_extraction("image-1", "ticket-1", "vision-test")
    await set_image_extraction_status("image-1", "processing", increment_attempt=True)
    await set_image_extraction_status(
        "image-1", "completed", text="Guia DAS com vencimento em 30/07."
    )
    row = await get_image_extraction("image-1")
    assert row is not None
    assert row["status"] == "completed"
    assert row["attempt_count"] == 1
    assert await get_completed_image_extractions(["image-1"]) == {
        "image-1": "Guia DAS com vencimento em 30/07."
    }
    assert await get_completed_transcriptions(["missing"]) == {}
    assert await get_pending_content_extractions(["image-1"], ["image-1"]) == set()


@pytest.mark.asyncio
async def test_protocol_update_is_idempotent():
    await insert_classification(
        conversation_id="conversation-1",
        message_ids=[],
        created_at="2026-07-22T12:00:00+00:00",
        full_context="Cliente: ajuda",
        message_count=1,
        result={"intent_type": "request", "title": "Ajuda"},
        model="test",
        processing_time_ms=1,
        prompt_version="v2",
    )
    assert await update_analysis_protocol("conversation-1", "2026072212345")
    assert await update_analysis_protocol("conversation-1", "2026072212345")
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT protocol FROM ia_classifications"
        ).fetchone() == ("2026072212345",)


@pytest.mark.asyncio
async def test_invalid_intent_is_persisted_as_other():
    await insert_classification(
        conversation_id="invalid-intent",
        message_ids=[],
        created_at="2026-07-22T12:00:00+00:00",
        full_context="Cliente: ajuda",
        message_count=1,
        result={"intent_type": "not-a-category"},
        model="test",
        processing_time_ms=1,
        prompt_version="v2",
    )
    with psycopg.connect(settings.database_url) as connection:
        assert connection.execute(
            "SELECT intent_type FROM ia_classifications"
        ).fetchone() == ("other",)
