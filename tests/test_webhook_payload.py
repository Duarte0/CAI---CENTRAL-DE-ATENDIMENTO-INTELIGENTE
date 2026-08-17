from src.core.models import WebhookPayload


def test_extracts_direct_digisac_fields():
    payload = WebhookPayload.model_validate(
        {
            "event": "message.created",
            "conversation_id": "conv-1",
            "message_id": "msg-1",
            "content": "Olá",
            "sender_id": "customer-1",
        }
    )

    assert payload.get_conversation_id() == "conv-1"
    assert payload.get_message_id() == "msg-1"
    assert payload.get_content() == "Olá"
    assert payload.get_sender_id() == "customer-1"


def test_extracts_data_envelope_and_keeps_unknown_fields():
    payload = WebhookPayload.model_validate(
        {
            "webhook_name": "new_message",
            "data": {
                "conversationId": "conv-2",
                "messageId": "msg-2",
                "text": "Preciso de ajuda",
                "senderId": "customer-2",
            },
        }
    )

    assert payload.get_conversation_id() == "conv-2"
    assert payload.get_message_id() == "msg-2"
    assert payload.get_content() == "Preciso de ajuda"
    assert payload.get_sender_id() == "customer-2"
    assert payload.model_extra["webhook_name"] == "new_message"


def test_extracts_message_envelope_and_reports_sources():
    payload = WebhookPayload.model_validate(
        {
            "message": {
                "conversation_id": "conv-3",
                "id": "msg-3",
                "body": "Mensagem dentro do envelope",
                "fromId": "customer-3",
            }
        }
    )

    assert payload.get_conversation_id() == "conv-3"
    assert payload.get_message_id() == "msg-3"
    assert payload.get_content() == "Mensagem dentro do envelope"
    assert payload.get_sender_id() == "customer-3"
    extraction = payload.extraction_debug()
    assert extraction["content"] == {
        "present": True,
        "type": "str",
        "source": "$.message.body",
    }
    assert all("value" not in field for field in extraction.values())
