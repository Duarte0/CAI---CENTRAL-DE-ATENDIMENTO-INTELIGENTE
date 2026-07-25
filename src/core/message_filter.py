"""Shared message-origin rules used at ingestion and classification time."""


def is_bot_message(
    *,
    is_from_bot: bool | None,
    origin: str | None,
) -> bool:
    """Return whether Digisac identified a message as bot-generated."""
    if is_from_bot is True:
        return True
    if isinstance(origin, str) and origin.strip().lower() == "bot":
        return True
    return False
