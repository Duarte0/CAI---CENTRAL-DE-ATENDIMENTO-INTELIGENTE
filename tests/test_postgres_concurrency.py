import asyncio

import psycopg
import pytest

from src.core.config import settings
from src.core.db import (
    insert_classification,
    reserve_image_extraction,
    reserve_transcription,
    record_ticket_assignment,
)

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_concurrent_api_and_workers_do_not_lose_writes():
    async def classifications(worker):
        await asyncio.gather(
            *[
                insert_classification(
                    conversation_id=f"concurrent-{worker}-{index}",
                    message_ids=[],
                    created_at="2026-07-24T10:00:00+00:00",
                    full_context="",
                    message_count=0,
                    result={"intent_type": "other"},
                    model="test",
                    processing_time_ms=1,
                    prompt_version="test",
                )
                for index in range(10)
            ]
        )

    async def assignments(worker):
        await asyncio.gather(
            *[
                record_ticket_assignment(
                    conversation_id=f"assignment-{worker}-{index}",
                    department_id="department",
                    user_id="user",
                    event_timestamp="2026-07-24T10:00:00+00:00",
                    event_key=f"event-{worker}-{index}",
                )
                for index in range(10)
            ]
        )

    await asyncio.gather(
        classifications("ia"),
        assignments("api"),
        asyncio.gather(
            *[
                reserve_transcription(f"audio-{index}", "ticket", "whisper")
                for index in range(10)
            ]
        ),
        asyncio.gather(
            *[
                reserve_image_extraction(f"image-{index}", "ticket", "vision")
                for index in range(10)
            ]
        ),
    )
    with psycopg.connect(settings.database_url) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM ia_classifications),
                (SELECT COUNT(*) FROM ticket_assignment_history),
                (SELECT COUNT(*) FROM message_transcriptions),
                (SELECT COUNT(*) FROM message_image_extractions)
            """
        ).fetchone()
    assert counts == (10, 10, 10, 10)
