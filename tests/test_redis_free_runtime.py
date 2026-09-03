"""Regression tests for the PostgreSQL-only application runtime boundary."""

from pathlib import Path

import pytest

from src.api import routes


@pytest.mark.asyncio
async def test_health_requires_postgresql_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def ready() -> bool:
        calls.append("postgres")
        return True

    monkeypatch.setattr(routes, "database_is_ready", ready)

    assert await routes.health() == {"status": "ok"}
    assert calls == ["postgres"]


@pytest.mark.asyncio
async def test_queue_metrics_are_durable_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def cycle_work() -> dict[str, int]:
        return {"due": 1, "scheduled": 2, "leased": 3}

    async def audio_work() -> dict[str, int]:
        return {
            "due": 4,
            "scheduled": 5,
            "leased": 6,
            "stale": 7,
            "completed": 8,
            "failed": 9,
        }

    async def image_work() -> dict[str, int]:
        return {
            "due": 10,
            "scheduled": 11,
            "leased": 12,
            "stale": 13,
            "completed": 14,
            "failed": 15,
        }

    async def cycle_metrics() -> dict[str, int]:
        return {"completed": 16}

    monkeypatch.setattr(routes, "get_cycle_work_metrics", cycle_work)
    monkeypatch.setattr(routes, "get_transcription_work_metrics", audio_work)
    monkeypatch.setattr(routes, "get_image_extraction_work_metrics", image_work)
    monkeypatch.setattr(routes, "get_cycle_metrics", cycle_metrics)

    result = await routes.queue_metrics()

    assert result == {
        "audio_due": 4,
        "audio_scheduled": 5,
        "audio_leased": 6,
        "audio_stale": 7,
        "audio_completed": 8,
        "audio_failed": 9,
        "image_due": 10,
        "image_scheduled": 11,
        "image_leased": 12,
        "image_stale": 13,
        "image_completed": 14,
        "image_failed": 15,
        "ia_due": 1,
        "ia_scheduled": 2,
        "ia_leased": 3,
        "conversation_cycles": {"completed": 16},
    }


def test_runtime_has_no_redis_dependency_and_compose_has_no_redis_service() -> None:
    routes_source = Path("src/api/routes.py").read_text(encoding="utf-8").lower()
    ia_worker_source = Path("src/workers/ia_worker.py").read_text(encoding="utf-8").lower()
    config_source = Path("src/core/config.py").read_text(encoding="utf-8").lower()
    compose_source = Path("docker-compose.yml").read_text(encoding="utf-8")
    requirements = Path("requirements.txt").read_text(encoding="utf-8").splitlines()

    assert "redis" not in routes_source
    assert "redis" not in ia_worker_source
    assert "redis" not in config_source
    assert "  redis:" not in compose_source
    assert "redis_data" not in compose_source
    assert "\n      REDIS_URL:" not in compose_source
    assert not any(line.strip().startswith("redis==") for line in requirements)
    assert not Path("src/core/redis_client.py").exists()
    assert "MAINTENANCE_REDIS_URL" in compose_source


def test_redis_client_and_historical_sources_are_maintenance_only() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    maintenance_requirements = Path("requirements-maintenance.txt").read_text(
        encoding="utf-8"
    )

    api_stage, maintenance_stage = dockerfile.split("FROM api AS maintenance", 1)
    assert "requirements-maintenance.txt" not in api_stage
    assert "redis==" not in api_stage.lower()
    assert "requirements-maintenance.txt" in maintenance_stage
    assert "redis==5.0.1" in maintenance_requirements
    assert Path("scripts/redis_maintenance_client.py").exists()
