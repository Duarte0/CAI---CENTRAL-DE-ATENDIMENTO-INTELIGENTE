# src/core/models.py
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Any, Dict, List, Literal, Optional, cast
from datetime import datetime, timezone
from enum import Enum



class MessageEventType(str, Enum):
    MESSAGE_RECEIVED = "message.received"
    MESSAGE_CREATED = "message.created"
    CONVERSATION_CREATED = "conversation.created"
    CONVERSATION_UPDATED = "conversation.updated"


class WebhookPayload(BaseModel):
    """Payload tolerante a variações de estrutura enviadas pelo Digisac."""

    event: Optional[Any] = None
    conversation_id: Optional[Any] = None
    message_id: Optional[Any] = None
    content: Optional[Any] = None
    sender_id: Optional[Any] = None
    timestamp: Optional[Any] = None
    data: Optional[Any] = None
    message: Optional[Any] = None
    metadata: Optional[Any] = None

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    @model_validator(mode="before")
    @classmethod
    def payload_must_be_an_object(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("Webhook payload must be a JSON object")
        return cast(Dict[str, Any], value)

    def _containers(self) -> List[tuple[str, Dict[str, Any]]]:
        """Mappings in preference order: direct fields, ``data``, ``message``."""
        root = self.model_dump(mode="python")
        containers: List[tuple[str, Dict[str, Any]]] = [("$", root)]
        for name in ("data", "message"):
            value = root.get(name)
            if isinstance(value, dict):
                containers.append((f"$.{name}", cast(Dict[str, Any], value)))
        for parent, value in list(containers[1:]):
            for name in (
                "data",
                "message",
                "payload",
                "conversation",
                "sender",
                "contact",
                "author",
            ):
                nested = value.get(name)
                if isinstance(nested, dict):
                    containers.append(
                        (f"{parent}.{name}", cast(Dict[str, Any], nested))
                    )
        return containers

    def _extract(self, keys: List[str]) -> tuple[Optional[Any], Optional[str]]:
        for path, container in self._containers():
            for key in keys:
                value = container.get(key)
                if value not in (None, "") and not isinstance(value, (dict, list)):
                    return value, f"{path}.{key}"
        return None, None

    def get_conversation_id(self) -> Optional[str]:
        value, _ = self._extract(
            [
                "conversation_id",
                "conversationId",
                "conversation_uuid",
                "ticketId",
                "chat_id",
                "chatId",
            ]
        )
        return str(value) if value is not None else None

    def get_message_id(self) -> Optional[str]:
        value, _ = self._extract(
            ["message_id", "messageId", "message_uuid", "id", "uuid"]
        )
        return str(value) if value is not None else None

    def get_content(self) -> str:
        if isinstance(self.message, str) and self.message.strip():
            return self.message
        value, _ = self._extract(
            ["content", "text", "body", "message_text", "messageText", "textContent"]
        )
        return str(value) if value is not None else ""

    def get_sender_id(self) -> Optional[str]:
        value, _ = self._extract(
            [
                "sender_id",
                "senderId",
                "from_id",
                "fromId",
                "author_id",
                "authorId",
                "user_id",
            ]
        )
        return str(value) if value is not None else None

    def get_message_type(self) -> str:
        value, _ = self._extract(["message_type", "messageType", "type"])
        return str(value) if value is not None else "chat"

    def get_file(self) -> Dict[str, Any]:
        for _, container in self._containers():
            value = container.get("file")
            if isinstance(value, dict):
                return cast(Dict[str, Any], value)
        return {}

    def get_event(self) -> str:
        value, _ = self._extract(["event", "event_type", "eventType", "type"])
        return (
            str(value) if value is not None else MessageEventType.MESSAGE_CREATED.value
        )

    def get_timestamp(self) -> Optional[datetime]:
        value, _ = self._extract(
            ["timestamp", "created_at", "createdAt", "sent_at", "sentAt"]
        )
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def extraction_debug(self) -> Dict[str, Any]:
        """Values and source paths, suitable for structured debug logging."""
        fields = {
            "conversation_id": [
                "conversation_id",
                "conversationId",
                "conversation_uuid",
                "ticketId",
                "chat_id",
                "chatId",
            ],
            "message_id": ["message_id", "messageId", "message_uuid", "id", "uuid"],
            "content": [
                "content",
                "text",
                "body",
                "message_text",
                "messageText",
                "textContent",
            ],
            "sender_id": [
                "sender_id",
                "senderId",
                "from_id",
                "fromId",
                "author_id",
                "authorId",
                "user_id",
            ],
            "event": ["event", "event_type", "eventType", "type"],
            "timestamp": ["timestamp", "created_at", "createdAt", "sent_at", "sentAt"],
        }
        result: Dict[str, Any] = {}
        for name, keys in fields.items():
            value, source = self._extract(keys)
            result[name] = {"value": value, "source": source}
        if isinstance(self.message, str) and self.message.strip():
            result["content"] = {"value": self.message, "source": "$.message"}
        return result


# Backwards-compatible name for callers that still import the old model.
DigisacWebhookPayload = WebhookPayload


class IAAnalysisResult(BaseModel):
    """Resultado da análise da IA"""

    intent_type: Literal[
        "question", "problem", "request", "complaint", "payment", "billing",
        "financial", "document", "protocol", "other",
    ]
    confidence: float
    title: Optional[str] = None
    protocol: Optional[str] = None
    display_title: Optional[str] = None
    description: Optional[str] = None
    department: List[str] = Field(default_factory=list)
    agent: List[str] = Field(default_factory=list)
    processed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    message_count: int = 0

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "intent_type": "question",
                "confidence": 0.98,
                "title": "Emissão de DARF",
                "description": "Cliente não conseguiu emitir DARF do período de junho",
                "department": ["Atendimento", "Departamento Fiscal"],
                "agent": ["Jaqueline Oliveira", "Carlos Silva"],
                "message_count": 3,
            }
        }
    )


class ProcessingStatus(str, Enum):
    OPEN = "open"
    PENDING = "pending"
    PROCESSING = "processing"
    RECOVERING_MESSAGES = "recovering_messages"
    WAITING_MEDIA = "waiting_media"
    MEDIA_BLOCKED = "media_blocked"
    BUILDING_CONTEXT = "building_context"
    SUMMARIZING = "summarizing"
    CLASSIFYING = "classifying"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    RETRYABLE_FAILURE = "retryable_failure"
    FAILED = "failed"


class ConversationProcessing(BaseModel):
    """Status do processamento de uma conversa"""

    conversation_id: str
    cycle_id: Optional[str] = None
    status: ProcessingStatus = ProcessingStatus.PENDING
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    result: Optional[IAAnalysisResult] = None
    retry_count: int = 0
    transient_retry_count: int = 0
    max_retries: int = 3
