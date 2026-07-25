import json
import logging
from typing import Any, cast

from fastapi import APIRouter, Request

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/debug/webhook")
async def debug_webhook(request: Request) -> dict[str, Any]:
    """Endpoint para debug - mostra exatamente o que o Digisac envia"""
    print("=" * 70)
    print("🔍 DEBUG - PAYLOAD RECEBIDO DO DIGISAC")
    print("=" * 70)

    # Headers
    headers = dict(request.headers)
    print("📋 HEADERS:")
    for key, value in headers.items():
        print(f"  {key}: {value}")

    # Body
    body = await request.body()
    print(f"\n📦 BODY BRUTO: {body}")

    # Tenta parsear JSON
    data: Any = None
    try:
        parsed = await request.json()
        data = parsed
        print(f"\n✅ JSON PARSEADO:")
        print(json.dumps(data, indent=2, ensure_ascii=False))

        # Mostra estrutura
        print(f"\n📊 ESTRUTURA:")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        data = cast(dict[str, Any], data)
        print(f"  Campos principais: {list(data.keys())}")

        nested_data = data.get("data")
        if isinstance(nested_data, dict):
            nested_data = cast(dict[str, Any], nested_data)
            print(f"  data campos: {list(nested_data.keys())}")

        message = data.get("message")
        if isinstance(message, dict):
            message = cast(dict[str, Any], message)
            print(f"  message campos: {list(message.keys())}")

        messages = data.get("messages")
        if isinstance(messages, list):
            messages = cast(list[Any], messages)
            print(f"  messages é lista com {len(messages)} itens")
            if messages and isinstance(messages[0], dict):
                first_message = cast(dict[str, Any], messages[0])
                print(f"  messages[0] campos: {list(first_message.keys())}")

    except Exception as e:
        print(f"\n❌ ERRO AO PARSEAR JSON: {e}")
        print(f"Conteúdo: {body.decode('utf-8', errors='ignore')}")

    print("=" * 70)

    return {
        "status": "debug_received",
        "headers": headers,
        "body": body.decode('utf-8', errors='ignore'),
        "parsed": data
    }
