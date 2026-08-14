from __future__ import annotations

import pytest

from src.core.department_mapping import configure_department_mapping


@pytest.mark.asyncio
async def test_manual_mapping_rejects_unsanitized_reason_before_database_access() -> None:
    with pytest.raises(ValueError, match="reason"):
        await configure_department_mapping(
            "digisac-unit",
            "acessorias-unit",
            reason="raw value with spaces",
        )


@pytest.mark.asyncio
async def test_manual_mapping_rejects_sensitive_metadata_keys_before_database_access() -> None:
    with pytest.raises(ValueError, match="metadata"):
        await configure_department_mapping(
            "digisac-unit",
            "acessorias-unit",
            reason="approved_route",
            metadata={"secret": "not-safe"},
        )
