"""Shared media-type detection for normalized DigiSac messages."""

from typing import Any, Mapping


def is_image_message(message_type: Any, file_data: Any = None) -> bool:
    """Return whether a message contains an image, including image documents."""
    if message_type == "image":
        return True
    if message_type != "document" or not isinstance(file_data, Mapping):
        return False
    mimetype = file_data.get("mimetype")
    return isinstance(mimetype, str) and mimetype.strip().lower().startswith(
        "image/"
    )


def effective_message_type(message_type: str, file_data: Any = None) -> str:
    """Normalize DigiSac document-wrapped images to the internal image type."""
    return "image" if is_image_message(message_type, file_data) else message_type
