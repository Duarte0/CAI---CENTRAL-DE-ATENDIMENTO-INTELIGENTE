# utils/idempotency.py
import hashlib
import json
from typing import Any, Dict

from src.core.redis_client import AsyncRedis


class IdempotencyService:
    def __init__(self, redis_client: AsyncRedis):
        self.redis = redis_client
        self.ttl = 3600  # 1 hora

    async def is_processed(self, event_id: str) -> bool:
        """Verifica se evento já foi processado"""
        key = f"processed:{event_id}"
        return await self.redis.exists(key) > 0

    async def mark_processed(self, event_id: str) -> None:
        """Marca evento como processado"""
        key = f"processed:{event_id}"
        await self.redis.setex(key, self.ttl, "1")

    async def try_mark_processed(self, event_id: str) -> bool:
        """Atomically reserve an event; False means it was already received."""
        return bool(
            await self.redis.set(f"processed:{event_id}", "1", ex=self.ttl, nx=True)
        )

    @staticmethod
    def generate_event_id(payload: Dict[str, Any]) -> str:
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
