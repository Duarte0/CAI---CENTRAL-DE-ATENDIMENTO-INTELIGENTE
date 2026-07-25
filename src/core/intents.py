"""Shared intent taxonomy and normalization for model and persistence boundaries."""

import re
import unicodedata
from typing import Any


VALID_INTENT_TYPES = (
    "question",
    "problem",
    "request",
    "complaint",
    "payment",
    "billing",
    "financial",
    "document",
    "protocol",
    "other",
)

_INTENT_ALIASES = {
    "pergunta": "question",
    "duvida": "question",
    "problema": "problem",
    "erro": "problem",
    "solicitacao": "request",
    "pedido": "request",
    "reclamacao": "complaint",
    "insatisfacao": "complaint",
    "pagamento": "payment",
    "pagar": "payment",
    "taxa": "payment",
    "cobranca": "billing",
    "boleto": "billing",
    "financeiro": "financial",
    "financas": "financial",
    "documento": "document",
    "documentos": "document",
    "envio_de_documento": "document",
    "protocolo": "protocol",
    "protocolar": "protocol",
    "outro": "other",
    "outros": "other",
}


def normalize_intent_type(value: Any) -> str | None:
    """Return a canonical intent for harmless language/case/format variants."""
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    normalized = "".join(
        char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    if normalized in VALID_INTENT_TYPES:
        return normalized
    return _INTENT_ALIASES.get(normalized)
