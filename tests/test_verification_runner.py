from __future__ import annotations

import os

import pytest

from scripts.verify import (
    TEST_DATABASE,
    TEST_PASSWORD,
    TEST_USER,
    build_network_target_url,
    build_target_url,
    parse_compose_endpoint,
    prepare_test_environment,
    validate_runner_target,
)


def test_parse_compose_endpoint_accepts_docker_compose_output() -> None:
    assert parse_compose_endpoint("127.0.0.1:49152\n") == 49152


def test_build_target_url_is_local_and_uses_test_database() -> None:
    assert build_target_url(49152) == (
        f"postgresql://{TEST_USER}:{TEST_PASSWORD}@127.0.0.1:49152/{TEST_DATABASE}"
    )


def test_build_network_target_url_uses_internal_service_name() -> None:
    assert build_network_target_url() == (
        f"postgresql://{TEST_USER}:{TEST_PASSWORD}@postgres-test:5432/{TEST_DATABASE}"
    )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://developer:secret@localhost:49152/cai_test",
        "postgresql://cai_test:cai_test@example.test:49152/cai_test",
        "postgresql://cai_test:cai_test@127.0.0.1:49152/production",
        "postgresql://cai_test:cai_test@127.0.0.1:5433/cai_test",
    ],
)
def test_validate_runner_target_rejects_non_runner_targets(url: str) -> None:
    with pytest.raises(ValueError):
        validate_runner_target(url, expected_port=49152)


def test_prepare_test_environment_overrides_external_database_values() -> None:
    source = {
        "CAI_TEST_DATABASE_URL": "postgresql://external/unsafe",
        "DATABASE_URL": "postgresql://external/unsafe",
        "DIGISAC_HISTORY_FINALIZATION_ENABLED": "false",
        "PATH": os.environ.get("PATH", ""),
    }

    prepared = prepare_test_environment(source, "postgresql://cai_test:cai_test@127.0.0.1:49152/cai_test")

    assert prepared["CAI_TEST_DATABASE_URL"].endswith("/cai_test")
    assert prepared["DATABASE_URL"] == prepared["CAI_TEST_DATABASE_URL"]
    assert prepared["DIGISAC_HISTORY_FINALIZATION_ENABLED"] == "true"
