from datetime import datetime, timedelta, timezone

from src.core.models import MessageBuffer, format_message_for_context
from src.core.analysis import build_display_title, with_protocol


def test_message_buffer_builds_chronological_context_for_both_parties():
    buffer = MessageBuffer(conversation_id="42")
    buffer.add_message({"content": "Olá", "is_from_me": False})
    buffer.add_message({"content": "Como posso ajudar?", "is_from_me": True})
    buffer.add_message({"content": "  Preciso de ajuda? ", "is_from_me": False})

    assert buffer.message_count == 3
    assert (
        buffer.get_consolidated_context()
        == "Cliente: Olá\nAtendente: Como posso ajudar?\nCliente: Preciso de ajuda?"
    )


def test_message_buffer_expiration_uses_total_seconds():
    buffer = MessageBuffer(conversation_id="42")
    buffer.last_activity = datetime.now(timezone.utc) - timedelta(seconds=31)
    assert buffer.is_expired(30)


def test_formats_supported_media_for_customer_and_attendant():
    cases = [
        ({"message_type": "document", "file": {"name": "Documento.pdf"}, "is_from_me": False}, 'Cliente: enviou um documento chamado "Documento.pdf".'),
        ({"message_type": "document", "file": {"name": "Documento.pdf"}, "is_from_me": True}, 'Atendente: enviou um documento chamado "Documento.pdf".'),
        ({"message_type": "document", "file": {}, "is_from_me": False}, "Cliente: enviou um documento."),
        ({"message_type": "document", "file": {"public_filename": "Apuração.pdf"}, "is_from_me": False}, 'Cliente: enviou um documento chamado "Apuração.pdf".'),
        ({"message_type": "ptt", "is_from_me": False}, "Cliente: enviou um áudio."),
        ({"message_type": "ptt", "is_from_me": True}, "Atendente: enviou um áudio."),
        ({"message_type": "image", "is_from_me": False}, "Cliente: enviou uma imagem."),
    ]
    for message, expected in cases:
        assert format_message_for_context(message) == expected


def test_media_caption_preserves_text_and_attachment_description():
    message = {"message_type": "document", "text": "Segue a apuração", "file": {"name": "IRPJ e CSLL.pdf"}, "is_from_me": False}
    assert format_message_for_context(message) == 'Cliente: Segue a apuração\nCliente: enviou um documento chamado "IRPJ e CSLL.pdf".'


def test_completed_audio_transcriptions_are_rendered_in_chronological_position():
    buffer = MessageBuffer(
        conversation_id="42",
        messages=[
            {"message_id": "audio-customer", "message_type": "ptt", "is_from_me": False,
             "timestamp": "2026-07-22T12:00:00Z"},
            {"message_id": "text-agent", "content": "Entendi", "is_from_me": True,
             "timestamp": "2026-07-22T12:01:00Z"},
            {"message_id": "audio-agent", "message_type": "audio", "is_from_me": True,
             "timestamp": "2026-07-22T12:02:00Z"},
        ],
    )

    context = buffer.get_consolidated_context({
        "audio-customer": "Quero tratar da rescisão.",
        "audio-agent": "O aviso prévio será calculado.",
    })

    assert context == (
        "Cliente: [áudio transcrito] Quero tratar da rescisão.\n"
        "Atendente: Entendi\n"
        "Atendente: [áudio transcrito] O aviso prévio será calculado."
    )


def test_audio_without_completed_transcription_keeps_placeholder():
    buffer = MessageBuffer(
        conversation_id="42",
        messages=[{"message_id": "audio-pending", "message_type": "ptt", "is_from_me": False}],
    )

    assert buffer.get_consolidated_context({}) == "Cliente: enviou um áudio."


def test_completed_image_extraction_is_rendered_for_both_authors():
    buffer = MessageBuffer(
        conversation_id="42",
        messages=[
            {
                "message_id": "image-customer",
                "message_type": "image",
                "is_from_me": False,
                "timestamp": "2026-07-22T12:00:00Z",
            },
            {
                "message_id": "image-agent",
                "message_type": "image",
                "is_from_me": True,
                "timestamp": "2026-07-22T12:01:00Z",
            },
        ],
    )

    context = buffer.get_consolidated_context(
        image_extractions={
            "image-customer": "Comprovante de R$ 150,00 pago em 20/07.",
            "image-agent": "Guia DAS com vencimento em 30/07.",
        }
    )

    assert context == (
        "Cliente: [imagem] Comprovante de R$ 150,00 pago em 20/07.\n"
        "Atendente: [imagem] Guia DAS com vencimento em 30/07."
    )


def test_image_without_completed_extraction_keeps_placeholder():
    buffer = MessageBuffer(
        conversation_id="42",
        messages=[
            {
                "message_id": "image-pending",
                "message_type": "image",
                "is_from_me": False,
            }
        ],
    )

    assert buffer.get_consolidated_context(
        image_extractions={}
    ) == "Cliente: enviou uma imagem."


def test_display_title_preserves_original_ai_title():
    result = {"title": " Emissão de NF ", "intent_type": "request"}
    enriched = with_protocol(result, "2026072223796")
    assert result["title"] == " Emissão de NF "
    assert enriched["title"] == " Emissão de NF "
    assert enriched["display_title"] == "[2026072223796] - Emissão de NF"
    assert build_display_title("Emissão de NF", None) == "Emissão de NF"
