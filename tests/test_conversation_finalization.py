from datetime import datetime, timezone

from src.core.finalization import (
    apply_media_states,
    chunk_messages,
    estimate_tokens,
    normalize_history,
    render_context,
)


UTC = timezone.utc


def raw(message_id, timestamp, **extra):
    return {
        "id": message_id,
        "timestamp": timestamp,
        "type": "chat",
        "text": message_id,
        "visible": True,
        "deletedAt": None,
        "isFromMe": False,
        "isFromBot": False,
        **extra,
    }


def test_filters_invalid_messages_and_orders_with_id_tiebreaker():
    closed = datetime(2026, 7, 28, 13, tzinfo=UTC)
    history = normalize_history(
        [
            raw("b", "2026-07-28T12:00:00Z"),
            raw("a", "2026-07-28T12:00:00Z"),
            raw("bot", "2026-07-28T12:01:00Z", isFromBot=True),
            raw("hidden", "2026-07-28T12:02:00Z", visible=False),
            raw("deleted", "2026-07-28T12:03:00Z", deletedAt="now"),
            raw("unknown", "2026-07-28T12:04:00Z", type="sticker"),
            raw(
                "open",
                "2026-07-28T11:00:00Z",
                type="ticket",
                data={"ticketOpen": True},
            ),
        ],
        cycle_started_at=None,
        previous_closed_at=None,
        ticket_closed_at=closed,
    )
    assert [item["message_id"] for item in history.messages] == ["a", "b"]
    assert history.cycle_start_strategy == "digisac_ticket_open_event"
    assert history.exclusion_counts == {
        "bot": 1,
        "invisible": 1,
        "deleted": 1,
        "unknown_type": 1,
        "technical_ticket_event": 1,
    }


def test_previous_cycle_boundary_prevents_overlap():
    history = normalize_history(
        [
            raw("old", "2026-07-28T12:00:00Z"),
            raw("new", "2026-07-28T12:10:00Z"),
        ],
        cycle_started_at=datetime(2026, 7, 28, 12, 5, tzinfo=UTC),
        previous_closed_at=datetime(2026, 7, 28, 12, 1, tzinfo=UTC),
        ticket_closed_at=datetime(2026, 7, 28, 12, 20, tzinfo=UTC),
    )
    assert [item["message_id"] for item in history.messages] == ["new"]


def test_media_terminal_audio_failure_blocks_without_marker_or_warning():
    messages = [
        {
            "message_id": "audio",
            "timestamp": "2026-07-28T12:00:00+00:00",
            "type": "ptt",
            "content": "",
        },
        {
            "message_id": "image",
            "timestamp": "2026-07-28T12:01:00+00:00",
            "type": "image",
            "content": "",
        },
    ]
    hydrated, warnings, pending, blocked = apply_media_states(
        messages,
        {
            "audio": {
                "status": "failed",
                "attempt_count": 3,
                "kind": "audio",
            },
            "image": {
                "status": "completed",
                "attempt_count": 1,
                "text": "boleto",
                "kind": "image",
            },
        },
        max_attempts=3,
    )
    assert not pending
    assert blocked == {"audio"}
    assert hydrated[0]["content"] == ""
    assert hydrated[1]["content"] == "boleto"
    assert warnings == []


def test_completed_media_without_text_is_not_ready_for_finalization():
    messages = [
        {"message_id": "audio", "type": "ptt", "content": ""},
    ]
    hydrated, warnings, pending, blocked = apply_media_states(
        messages,
        {
            "audio": {
                "status": "completed",
                "attempt_count": 1,
                "text": "   ",
                "kind": "audio",
            },
        },
        max_attempts=3,
    )
    assert hydrated[0]["content"] == ""
    assert pending == {"audio"}
    assert blocked == set()
    assert warnings == []


def test_pending_and_recoverable_failed_media_wait():
    messages = [
        {"message_id": "a", "type": "ptt", "content": ""},
        {"message_id": "b", "type": "image", "content": ""},
    ]
    _, _, pending, blocked = apply_media_states(
        messages,
        {
            "a": {"status": "pending", "attempt_count": 0},
            "b": {"status": "failed", "attempt_count": 1},
        },
        max_attempts=3,
    )
    assert pending == {"a", "b"}
    assert not blocked


def test_terminal_image_failure_blocks_finalization():
    messages = [
        {"message_id": "image", "type": "image", "content": ""},
    ]
    hydrated, warnings, pending, blocked = apply_media_states(
        messages,
        {
            "image": {
                "status": "failed",
                "attempt_count": 3,
                "kind": "image",
            },
        },
        max_attempts=3,
    )
    assert not pending
    assert blocked == {"image"}
    assert warnings == []
    assert hydrated[0]["content"] == ""


def test_render_context_has_document_quote_and_no_admin_headers_in_model():
    messages = [
        {
            "message_id": "one",
            "timestamp": "2026-07-28T11:00:00+00:00",
            "sender_type": "client",
            "sender_name": None,
            "type": "chat",
            "content": "Qual competência?",
            "quoted_message_id": None,
        },
        {
            "message_id": "two",
            "timestamp": "2026-07-28T11:01:00+00:00",
            "sender_type": "agent",
            "sender_name": "Valquíria",
            "type": "document",
            "content": "",
            "quoted_message_id": "one",
            "file": {"name": "contrato.pdf"},
        },
    ]
    audit = render_context(
        ticket_id="ticket",
        protocol="123",
        departments=["Paralegal"],
        agents=["Valquíria"],
        messages=messages,
        include_administrative_names=True,
    )
    model = render_context(
        ticket_id="ticket",
        protocol="123",
        departments=["Paralegal"],
        agents=["Valquíria"],
        messages=messages,
        include_administrative_names=False,
    )
    assert "DEPARTAMENTOS: Paralegal" in audit
    assert "DEPARTAMENTOS:" not in model
    assert "[EM RESPOSTA A: “Qual competência?”]" in model
    assert "[ATENDENTE ENVIOU O DOCUMENTO: contrato.pdf]" in model


def test_token_estimate_and_chunking_preserve_messages():
    messages = [{"message_id": str(index), "content": "x" * 60} for index in range(5)]
    chunks = chunk_messages(messages, token_limit=70)
    assert [item["message_id"] for chunk in chunks for item in chunk] == [
        "0",
        "1",
        "2",
        "3",
        "4",
    ]
    assert estimate_tokens("á" * 30) >= 20
