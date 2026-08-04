# src/core/models.py
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Any, Dict, List, Literal, Optional, cast
from datetime import datetime, timezone
from enum import Enum

from src.core.message_filter import is_bot_message
from src.core.media import is_image_message


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


class MessageBuffer(BaseModel):
    """Buffer de mensagens por conversa"""

    conversation_id: str
    messages: List[Dict[str, Any]] = []
    last_activity: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True
    message_count: int = 0

    def add_message(self, message_data: Dict[str, Any]) -> None:
        self.messages.append(message_data)
        self.message_count += 1
        self.last_activity = datetime.now(timezone.utc)

    def is_expired(self, timeout_seconds: int) -> bool:
        return (
            datetime.now(timezone.utc) - self.last_activity
        ).total_seconds() > timeout_seconds

    def get_consolidated_context(
        self,
        transcriptions: Optional[Dict[str, str]] = None,
        image_extractions: Optional[Dict[str, str]] = None,
    ) -> str:
        """Render the complete ticket context in chronological order."""
        context_parts: List[str] = []
        transcriptions = transcriptions or {}
        image_extractions = image_extractions or {}

        def chronological_key(msg: Dict[str, Any]) -> tuple[float, str]:
            epoch = msg.get("timestamp_epoch")
            if isinstance(epoch, (int, float)):
                return float(epoch), str(msg.get("id", msg.get("message_id", "")))
            value = msg.get("timestamp")
            if isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(
                        value.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return parsed.timestamp(), str(msg.get("id", msg.get("message_id", "")))
                except ValueError:
                    pass
            return 0.0, str(msg.get("id", msg.get("message_id", "")))

        for msg in sorted(self.messages, key=chronological_key):
            message_id = msg.get("id", msg.get("message_id"))
            transcription = (
                transcriptions.get(str(message_id))
                if message_id not in (None, "")
                else None
            )
            image_extraction = (
                image_extractions.get(str(message_id))
                if message_id not in (None, "")
                else None
            )
            formatted = format_message_for_context(
                msg,
                transcription=transcription,
                image_extraction=image_extraction,
            )
            if formatted:
                context_parts.append(formatted)
        return "\n".join(context_parts)

    def human_messages(self) -> List[Dict[str, Any]]:
        """Exclude bot messages, including entries left by older deployments."""
        return [
            message
            for message in self.messages
            if not is_bot_message(
                is_from_bot=message.get(
                    "is_from_bot", message.get("isFromBot")),
                origin=message.get("origin"),
            )
        ]

    def human_buffer(self) -> "MessageBuffer":
        messages = self.human_messages()
        return self.model_copy(update={"messages": messages, "message_count": len(messages)})

    def get_message_ids(self) -> List[str]:
        """Return the identifiers available for messages in this buffer."""
        return [
            str(message_id)
            for message in self.messages
            if (message_id := message.get("id", message.get("message_id"))) not in (None, "")
        ]

    def get_audio_message_ids(self) -> List[str]:
        """Return only audio message ids that may have transcription rows."""
        return [
            str(message_id)
            for message in self.messages
            if message.get("message_type", "chat") in {"ptt", "audio", "voice"}
            and (message_id := message.get("id", message.get("message_id")))
            not in (None, "")
        ]

    def get_image_message_ids(self) -> List[str]:
        """Return image message ids that may have visual extraction rows."""
        return [
            str(message_id)
            for message in self.messages
            if is_image_message(
                message.get("message_type", "chat"), message.get("file")
            )
            and (message_id := message.get("id", message.get("message_id")))
            not in (None, "")
        ]


def format_message_for_context(
    message: Dict[str, Any],
    *,
    transcription: Optional[str] = None,
    image_extraction: Optional[str] = None,
) -> Optional[str]:
    """Render one normalized human message without exposing attachment URLs."""
    role = (
        "Atendente"
        if message.get("isFromMe", message.get("is_from_me", False))
        else "Cliente"
    )
    message_type = message.get("message_type", "chat")
    raw_text = message.get("text", message.get("content", ""))
    text = raw_text.strip() if isinstance(raw_text, str) else ""

    lines = [f"{role}: {text}"] if text else []
    if is_image_message(message_type, message.get("file")):
        extracted_text = image_extraction.strip() if image_extraction else ""
        if extracted_text:
            lines.append(f"{role}: [imagem] {extracted_text}")
        else:
            lines.append(f"{role}: enviou uma imagem.")
    elif message_type == "document":
        raw_file = message.get("file")
        file_data = (
            cast(Dict[str, Any], raw_file)
            if isinstance(raw_file, dict)
            else {}
        )
        filename = file_data.get("name") or file_data.get("public_filename")
        if filename:
            lines.append(f'{role}: enviou um documento chamado "{filename}".')
        else:
            lines.append(f"{role}: enviou um documento.")
    elif message_type in {"ptt", "audio", "voice"}:
        transcript_text = transcription.strip() if isinstance(transcription, str) else ""
        if transcript_text:
            lines.append(f"{role}: [áudio transcrito] {transcript_text}")
        else:
            lines.append(f"{role}: enviou um áudio.")
    elif message_type != "chat":
        return None

    return "\n".join(lines) or None


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
