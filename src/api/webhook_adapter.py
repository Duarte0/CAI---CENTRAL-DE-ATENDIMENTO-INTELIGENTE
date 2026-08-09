"""Normalization of the Digisac ``message.created`` webhook payload."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, cast

from pydantic import BaseModel, ConfigDict, Field

from src.core.media import effective_message_type

SUPPORTED_MESSAGE_TYPES = {"chat", "document",
                           "ptt", "audio", "voice", "image"}
AUDIO_MESSAGE_TYPES = {"ptt", "audio", "voice"}
IMAGE_MESSAGE_TYPES = {"image"}


class DigisacMessage(BaseModel):
    """The fields used by the persistent-cycle and media pipelines."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    conversation_id: str = Field(alias="ticketId")
    message_id: Optional[str] = Field(default=None, alias="id")
    content: str = Field(default="", alias="text")
    message_type: str = Field(default="chat", alias="type")
    file: dict[str, Optional[str]] = Field(default_factory=dict)
    sender_id: Optional[str] = Field(default=None, alias="contactId")
    is_from_me: bool = Field(default=False, alias="isFromMe")
    is_from_bot: bool | None = Field(default=None, alias="isFromBot")
    origin: str | None = None
    user_id: Optional[str] = Field(default=None, alias="userId")
    event: str = "message.created"
    timestamp: Optional[datetime] = None

    def get_conversation_id(self) -> str:
        return self.conversation_id

    def get_message_id(self) -> Optional[str]:
        return self.message_id

    def get_content(self) -> str:
        return self.content

    def get_message_type(self) -> str:
        return self.message_type

    def get_file(self) -> dict[str, Optional[str]]:
        return self.file

    def get_sender_id(self) -> Optional[str]:
        return self.sender_id

    def get_event(self) -> str:
        return self.event

    def get_timestamp(self) -> Optional[datetime]:
        return self.timestamp


@dataclass(frozen=True, slots=True)
class AdaptationResult:
    """Result of adapting a webhook; ignored messages include a safe reason."""

    message: Optional[DigisacMessage] = None
    ignored_reason: Optional[str] = None

    @property
    def should_process(self) -> bool:
        return self.message is not None


class DigisacWebhookAdapter:
    """Adapt the confirmed Digisac envelope to the application's message shape.

    ``data.id`` maps to ``message_id``, ``data.text`` to ``content``,
    ``data.contactId`` to ``sender_id``, ``data.userId`` to ``user_id``, and
    ``data.ticketId`` to ``conversation_id``.  A ticket is required so history
    recovery remains scoped to its final Digisac ticket.
    """

    @classmethod
    def adapt(cls, payload: Mapping[str, Any]) -> AdaptationResult:
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return AdaptationResult(ignored_reason="missing_or_invalid_data")
        data = cast(Mapping[str, Any], data)

        is_from_me = data.get("isFromMe")
        if not isinstance(is_from_me, bool):
            return AdaptationResult(ignored_reason="missing_is_from_me")

        message_type = cls._as_non_empty_string(data.get("type")) or "chat"
        if message_type not in SUPPORTED_MESSAGE_TYPES:
            return AdaptationResult(ignored_reason="unsupported_message_type")

        text = data.get("text")
        content = text.strip() if isinstance(text, str) else ""
        if message_type == "chat" and not content:
            return AdaptationResult(ignored_reason="empty_message_text")

        conversation_id = cls._as_non_empty_string(data.get("ticketId"))
        if conversation_id is None:
            return AdaptationResult(ignored_reason="missing_ticket_id")

        raw_file = data.get("file")
        file_data: Mapping[str, Any] = (
            cast(Mapping[str, Any], raw_file)
            if isinstance(raw_file, Mapping)
            else {}
        )
        # Only safe metadata is normalized; signed URLs never enter durable
        # snapshots or queue payloads.
        normalized_file = {
            "id": cls._as_non_empty_string(file_data.get("id")),
            "name": cls._as_non_empty_string(file_data.get("name")),
            "public_filename": cls._as_non_empty_string(
                file_data.get("publicFilename")
            ),
            "extension": cls._as_non_empty_string(file_data.get("extension")),
            "mimetype": cls._as_non_empty_string(file_data.get("mimetype")),
        }

        event = payload.get("event")
        return AdaptationResult(
            message=DigisacMessage(
                ticketId=conversation_id,
                id=cls._as_non_empty_string(data.get("id")),
                text=content,
                type=effective_message_type(message_type, normalized_file),
                file=normalized_file,
                contactId=cls._as_non_empty_string(data.get("contactId")),
                isFromMe=is_from_me,
                isFromBot=(
                    data.get("isFromBot")
                    if isinstance(data.get("isFromBot"), bool)
                    else None
                ),
                origin=(
                    data.get("origin")
                    if isinstance(data.get("origin"), str)
                    else None
                ),
                userId=cls._as_non_empty_string(data.get("userId")),
                event=event if isinstance(event, str) else "message.created",
                timestamp=cls._parse_timestamp(data.get("timestamp")),
            )
        )

    @staticmethod
    def _as_non_empty_string(value: Any) -> Optional[str]:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @staticmethod
    def _parse_timestamp(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
