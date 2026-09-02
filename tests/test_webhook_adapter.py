import pytest
from fastapi import HTTPException, Response

from src.api import routes
from src.api.webhook_adapter import DigisacMessage, DigisacWebhookAdapter
from src.core.message_filter import is_bot_message


def test_adapts_real_digisac_customer_message():
    result = DigisacWebhookAdapter.adapt(
        {
            "event": "message.created",
            "data": {
                "id": "message-1",
                "text": "Preciso de ajuda",
                "contactId": "contact-1",
                "ticketId": "ticket-1",
                "isFromMe": False,
                "timestamp": "2026-07-17T16:09:50.440Z",
            },
        }
    )

    assert result.should_process
    assert result.message.conversation_id == "ticket-1"
    assert result.message.message_id == "message-1"
    assert result.message.content == "Preciso de ajuda"
    assert result.message.sender_id == "contact-1"
    assert result.message.is_from_me is False


def test_accepts_attendant_messages_and_records_the_author():
    result = DigisacWebhookAdapter.adapt(
        {
            "data": {
                "text": "Oi",
                "ticketId": "ticket-1",
                "userId": "user-1",
                "isFromMe": True,
            }
        }
    )

    assert result.should_process
    assert result.message.is_from_me is True
    assert result.message.user_id == "user-1"


@pytest.mark.parametrize(
    ("data", "expected", "reason"),
    [
        ({"isFromMe": True, "isFromBot": True, "origin": "bot"}, True, "is_from_bot"),
        ({"isFromMe": True, "isFromBot": False, "origin": "user"}, False, None),
        ({"isFromMe": False, "isFromBot": False, "origin": "whatsapp"}, False, None),
        ({"isFromMe": True, "origin": "bot"}, True, "bot_origin_fallback"),
        ({"isFromMe": True}, False, None),
        (
            {"isFromMe": True, "isFromBot": False, "origin": "bot"},
            True,
            "bot_origin_fallback",
        ),
    ],
)
def test_identifies_bot_messages_without_discarding_humans(data, expected, reason):
    message_data = {"id": "message-1", "text": "Olá", "ticketId": "ticket-1", **data}

    assert (
        is_bot_message(
            is_from_bot=message_data.get("isFromBot"), origin=message_data.get("origin")
        )
        is expected
    )
    result = DigisacWebhookAdapter.adapt(
        {"event": "message.created", "data": message_data}
    )

    assert result.should_process
    assert result.message.is_from_me is data["isFromMe"]


@pytest.mark.parametrize(
    ("is_from_bot", "origin", "expected"),
    [
        (True, None, True),
        (False, "bot", True),
        (None, "BOT", True),
        (None, " bot ", True),
        (False, "user", False),
        (None, None, False),
    ],
)
def test_is_bot_message(is_from_bot, origin, expected):
    assert is_bot_message(is_from_bot=is_from_bot, origin=origin) is expected


def test_digisac_message_preserves_bot_fields_from_aliases():
    message = DigisacMessage.model_validate(
        {
            "id": "bot-1",
            "ticketId": "ticket-1",
            "text": "Automática",
            "isFromMe": True,
            "isFromBot": True,
            "origin": "bot",
        }
    )
    assert message.message_id == "bot-1"
    assert message.is_from_me is True
    assert message.is_from_bot is True
    assert message.origin == "bot"


def test_ignores_empty_messages_and_messages_without_ticket():
    no_ticket = DigisacWebhookAdapter.adapt(
        {"data": {"text": "Oi", "contactId": "contact-1", "isFromMe": False}}
    )
    empty_message = DigisacWebhookAdapter.adapt(
        {"data": {"text": " ", "contactId": "contact-1", "isFromMe": False}}
    )

    assert no_ticket.ignored_reason == "missing_ticket_id"
    assert empty_message.ignored_reason == "empty_message_text"


@pytest.mark.asyncio
async def test_webhook_returns_200_for_invalid_message_shape(
    monkeypatch,
):
    payload = {
        "event": "message.created",
        "data": {"isFromMe": "unknown", "text": "Mensagem interna"},
    }

    async def fake_parse(_request):
        return payload, None

    monkeypatch.setattr(routes, "parse_webhook_payload", fake_parse)
    response = Response()

    body = await routes.digisac_webhook(
        request=None,
        response=response,
        redis=None,
    )

    assert response.status_code == 200
    assert body == {"status": "ignored", "reason": "missing_is_from_me"}


@pytest.mark.asyncio
async def test_webhook_returns_400_only_when_data_is_not_an_object(monkeypatch):
    async def fake_parse(_request):
        return {"event": "message.created", "data": None}, None

    monkeypatch.setattr(routes, "parse_webhook_payload", fake_parse)

    with pytest.raises(HTTPException) as error:
        await routes.digisac_webhook(request=None, response=Response(), redis=None)

    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_audio_webhook_admits_transcription_without_redis_publication(monkeypatch):
    queued = []

    class Redis:
        async def set(self, *_args, **_kwargs):
            return True

        async def rpush(self, queue, item):
            queued.append((queue, item))

    async def fake_parse(_request):
        return {
            "event": "message.updated",
            "data": {
                "id": "audio-1",
                "ticketId": "ticket-1",
                "type": "ptt",
                "isFromMe": True,
            },
        }, None

    async def fake_reserve(*_args):
        return True

    monkeypatch.setattr(routes, "parse_webhook_payload", fake_parse)
    monkeypatch.setattr(routes, "reserve_transcription", fake_reserve)

    body = await routes.digisac_webhook(
        request=None, response=Response(), redis=Redis()
    )

    assert body["transcription_queued"] is True
    assert queued == []


@pytest.mark.asyncio
@pytest.mark.parametrize("is_from_me", [False, True])
async def test_image_webhook_admits_extraction_without_redis_publication(
    monkeypatch, is_from_me
):
    queued = []

    class Redis:
        async def set(self, *_args, **_kwargs):
            return True

        async def rpush(self, queue, item):
            queued.append((queue, item))

    async def fake_parse(_request):
        return {
            "event": "message.created",
            "data": {
                "id": "image-1",
                "ticketId": "ticket-1",
                "type": "image",
                "isFromMe": is_from_me,
                "file": {"mimetype": "image/png"},
            },
        }, None

    async def fake_reserve(*_args):
        return True

    monkeypatch.setattr(routes, "parse_webhook_payload", fake_parse)
    monkeypatch.setattr(routes, "reserve_image_extraction", fake_reserve)

    body = await routes.digisac_webhook(
        request=None, response=Response(), redis=Redis()
    )

    assert body["image_extraction_queued"] is True
    assert queued == []
