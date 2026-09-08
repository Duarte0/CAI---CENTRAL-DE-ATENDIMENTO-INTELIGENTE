from __future__ import annotations

import pytest

from scripts import dispose_retained_redis_storage as disposal


def make_target(
    *,
    attachments: tuple[tuple[str, str], ...] = (("cai-redis-1", "Exited (0)"),),
) -> disposal.TargetSnapshot:
    return disposal.TargetSnapshot(
        container_name="cai-redis-1",
        container_id="container-id",
        container_status="exited",
        container_project="cai",
        container_service="redis",
        mounted_volume="cai_redis_data",
        mount_destination="/data",
        volume_name="cai_redis_data",
        volume_mountpoint="/var/lib/docker/volumes/cai_redis_data/_data",
        volume_project="cai",
        volume_label="redis_data",
        attachments=attachments,
        compose_services=("api", "audio_worker", "ia_worker", "image_worker", "postgres"),
        compose_volumes=("postgres_data",),
    )


def test_target_report_is_stable_and_sanitized() -> None:
    target = make_target()
    report = target.report()

    assert target.fingerprint() == target.fingerprint()
    assert report["fingerprint"] == target.fingerprint()
    assert "password" not in str(report).lower()
    assert "raw" not in str(report).lower()


def test_active_attachment_is_detected() -> None:
    target = make_target(attachments=(("cai-redis-1", "running"),))

    assert target.active_attachments == (("cai-redis-1", "running"),)


def test_resolve_target_rejects_wrong_compose_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = {
        "Name": "/cai-redis-1",
        "Id": "container-id",
        "State": {"Status": "exited"},
        "Config": {
            "Labels": {
                "com.docker.compose.project": "other",
                "com.docker.compose.service": "redis",
            }
        },
        "Mounts": [
            {"Type": "volume", "Name": "cai_redis_data", "Destination": "/data"}
        ],
    }
    monkeypatch.setattr(disposal, "_json_inspect", lambda _kind, _name: container)

    with pytest.raises(disposal.DisposalSafetyError, match="labels"):
        disposal.resolve_target(
            project="cai", container="cai-redis-1", volume="cai_redis_data"
        )


def test_resolve_target_rejects_unrelated_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = {
        "Name": "/cai-redis-1",
        "Id": "container-id",
        "State": {"Status": "exited"},
        "Config": {
            "Labels": {
                "com.docker.compose.project": "cai",
                "com.docker.compose.service": "redis",
            }
        },
        "Mounts": [
            {"Type": "volume", "Name": "cai_redis_data", "Destination": "/data"}
        ],
    }
    volume = {
        "Name": "cai_redis_data",
        "Mountpoint": "/var/lib/docker/volumes/cai_redis_data/_data",
        "Labels": {
            "com.docker.compose.project": "cai",
            "com.docker.compose.volume": "redis_data",
        },
    }
    monkeypatch.setattr(
        disposal,
        "_json_inspect",
        lambda kind, _name: container if kind == "container" else volume,
    )
    monkeypatch.setattr(
        disposal,
        "_volume_attachments",
        lambda _volume: (("cai-redis-1", "Exited (0)"), ("cai-api-1", "Up 1 minute")),
    )

    with pytest.raises(disposal.DisposalSafetyError, match="unrelated"):
        disposal.resolve_target(
            project="cai", container="cai-redis-1", volume="cai_redis_data"
        )


def test_apply_requires_exact_confirmation() -> None:
    with pytest.raises(disposal.DisposalSafetyError, match="exact confirmation"):
        disposal._require_confirmation("yes", "cai-redis-1", "cai_redis_data")

    disposal._require_confirmation(
        "DISPOSE cai-redis-1 cai_redis_data", "cai-redis-1", "cai_redis_data"
    )
