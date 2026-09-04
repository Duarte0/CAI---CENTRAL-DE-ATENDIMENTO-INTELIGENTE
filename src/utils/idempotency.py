# utils/idempotency.py
import hashlib
import json
from typing import Any, Mapping

from src.core import webhook_event_repository


class IdempotencyService:
    async def try_mark_processed(self, event_id: str) -> bool:
        """Atomically reserve an event; False means it was already received."""
        return await webhook_event_repository.try_mark_webhook_event(event_id)

    @staticmethod
    def generate_event_id(payload: Mapping[str, Any]) -> str:
        """Gera ID único para evento baseado no conteúdo"""
        # Usa campos chave para evitar duplicação
        key_fields = {
            "conversation_id": payload.get("conversation_id"),
            "event": payload.get("event"),
            "message_id": payload.get("message_id") or payload.get("id"),
            "timestamp": payload.get("timestamp") or payload.get("created_at"),
            "content": payload.get("content"),
        }
        content = json.dumps(key_fields, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()
