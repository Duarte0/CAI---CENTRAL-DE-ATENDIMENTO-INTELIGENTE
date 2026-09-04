import pytest

from scripts.retire_validated_legacy_redis_queues import (
    RetirementSafetyError,
    _assert_complete,
    _assert_second_snapshot,
    _validate_dry_run_report,
)


def _inventories(*, ia: list[str] | None = None) -> dict[str, dict]:
    return {
        "ia": {
            "queues": {
                "ia_queue": {
                    "truncated": False,
                    "entry_digests": ia or ["ia-digest"],
                },
                "ia_dead_letter": {
                    "truncated": False,
                    "entry_digests": [],
                },
            }
        },
        "audio": {
            "queues": {
                "audio_transcription_queue": {
                    "truncated": False,
                    "entry_digests": ["audio-digest"],
                },
                "audio_transcription_dead_letter": {
                    "truncated": False,
                    "entry_digests": [],
                },
            }
        },
        "image": {
            "queues": {
                "image_extraction_queue": {
                    "truncated": False,
                    "entry_digests": ["image-digest"],
                },
                "image_extraction_dead_letter": {
                    "truncated": False,
                    "entry_digests": [],
                },
            }
        },
    }


def _report() -> dict:
    return {
        "report_version": 1,
        "mode": "dry_run",
        "metadata": {"operator": "operator", "revision": "abc1234", "max_items": 10},
        "runtime_before": {"healthy": True},
        "inventories": _inventories(),
    }


def test_second_snapshot_rejects_same_length_queue_replacement():
    current = _inventories(ia=["different-digest"])

    with pytest.raises(RetirementSafetyError, match="ia changed"):
        _assert_second_snapshot(_inventories(), current, set())


def test_second_snapshot_allows_only_families_already_retired_to_shrink():
    current = _inventories(ia=[])

    _assert_second_snapshot(_inventories(), current, {"ia"})


def test_incomplete_report_is_rejected_before_apply():
    inventories = _inventories()
    inventories["image"]["queues"]["image_extraction_queue"]["truncated"] = True

    with pytest.raises(RetirementSafetyError, match="truncated"):
        _assert_complete(inventories)


def test_apply_requires_the_same_operator_revision_and_complete_dry_run():
    _validate_dry_run_report(_report(), "operator", "abc1234", 10)

    with pytest.raises(RetirementSafetyError, match="operator/revision"):
        _validate_dry_run_report(_report(), "other", "abc1234", 10)
