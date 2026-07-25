"""Business rules for presenting persisted conversation analyses."""

from typing import Any, Mapping


def normalize_protocol(value: Any) -> str | None:
    """Return a non-empty protocol string without guessing missing values."""
    if value is None:
        return None
    protocol = str(value).strip()
    return protocol or None


def build_display_title(title: str | None, protocol: str | None) -> str | None:
    """Combine protocol and IA title for presentation without mutating either."""
    normalized_title = title.strip() if isinstance(title, str) else ""
    if not normalized_title:
        return None
    return f"[{protocol}] - {normalized_title}" if protocol else normalized_title


def with_protocol(result: Mapping[str, Any], protocol: str | None) -> dict[str, Any]:
    """Return an API/result representation enriched with its display title."""
    enriched = dict(result)
    enriched["protocol"] = protocol
    enriched["display_title"] = build_display_title(
        enriched.get("title"), protocol)
    return enriched
