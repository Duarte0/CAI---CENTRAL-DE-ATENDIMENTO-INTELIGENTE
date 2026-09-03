import asyncio
import inspect
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.core.config import settings
from src.core.provider_retry import (
    ProviderRetryWindowActive,
    TransientProviderError,
)
from src.workers import ia_worker


def worker_without_init() -> ia_worker.IAWorker:
    worker = object.__new__(ia_worker.IAWorker)
    worker.queue = "ia_queue"
    worker.dead_letter = "ia_dead_letter"
    worker.max_retries = 3
    worker.provider_blocked_until = 0.0
    worker.provider_window_resume_pending = False
    return worker


def test_ia_worker_has_no_active_redis_finalization_transport() -> None:
    source = inspect.getsource(ia_worker.IAWorker)
    assert "ia_queue" not in source
    assert "ia_dead_letter" not in source
    assert "ia_status:" not in source
    assert "ia_result:" not in source
    assert "self.redis" not in source


@pytest.mark.asyncio
async def test_transient_provider_failure_is_persisted_without_terminal_budget(
    monkeypatch,
):
    transitions = []

    async def transition(cycle_id, status, **kwargs):
        transitions.append((cycle_id, status, kwargs))
        return {}

    monkeypatch.setattr(ia_worker, "transition_cycle", transition)
    monkeypatch.setattr(settings, "ia_retry_base_seconds", 2.0)
    monkeypatch.setattr(settings, "ia_retry_max_delay_seconds", 900.0)
    monkeypatch.setattr(settings, "ia_retry_provider_margin_seconds", 1.0)
    worker = worker_without_init()
    before = time.time()

    await worker._record_cycle_failure(
        {
            "public_id": "cycle",
            "conversation_id": "ticket",
            "status": "classifying",
            "attempt_count": 2,
            "transient_retry_count": 4,
        },
        {"cycle_id": "cycle"},
        TransientProviderError(
            "Groq 429",
            retry_after_seconds=32.5,
        ),
    )

    assert len(transitions) == 1
    _, status, kwargs = transitions[0]
    assert status == "retryable_failure"
    fields = kwargs["fields"]
    assert fields["transient_retry_count"] == 5
    assert "attempt_count" not in fields
    assert fields["next_attempt_at"].timestamp() >= before + 33.5
    assert worker.provider_blocked_until >= before + 33.5


@pytest.mark.asyncio
async def test_high_cycle_backoff_does_not_extend_short_provider_cooldown(
    monkeypatch,
):
    transitions = []

    async def transition(cycle_id, status, **kwargs):
        transitions.append((cycle_id, status, kwargs))
        return {}

    monkeypatch.setattr(ia_worker, "transition_cycle", transition)
    monkeypatch.setattr(settings, "ia_retry_base_seconds", 2.0)
    monkeypatch.setattr(settings, "ia_retry_max_delay_seconds", 900.0)
    monkeypatch.setattr(settings, "ia_retry_provider_margin_seconds", 1.0)
    worker = worker_without_init()
    before = time.time()

    await worker._record_cycle_failure(
        {
            "public_id": "cycle",
            "conversation_id": "ticket",
            "status": "classifying",
            "transient_retry_count": 11,
        },
        {"cycle_id": "cycle"},
        TransientProviderError(
            "Groq 429",
            retry_after_seconds=2.445,
            category="rate_limit",
        ),
    )

    fields = transitions[0][2]["fields"]
    assert fields["transient_retry_count"] == 12
    assert fields["next_attempt_at"].timestamp() >= before + 900
    assert worker.provider_blocked_until == pytest.approx(
        before + 3.445,
        abs=0.1,
    )


@pytest.mark.asyncio
async def test_provider_cooldown_without_retry_after_uses_base_delay(
    monkeypatch,
):
    transitions = []

    async def transition(cycle_id, status, **kwargs):
        transitions.append((cycle_id, status, kwargs))
        return {}

    monkeypatch.setattr(ia_worker, "transition_cycle", transition)
    monkeypatch.setattr(settings, "ia_retry_base_seconds", 2.0)
    monkeypatch.setattr(settings, "ia_retry_max_delay_seconds", 900.0)
    monkeypatch.setattr(settings, "ia_retry_provider_margin_seconds", 1.0)
    worker = worker_without_init()
    before = time.time()

    await worker._record_cycle_failure(
        {
            "public_id": "cycle",
            "conversation_id": "ticket",
            "status": "classifying",
            "transient_retry_count": 11,
        },
        {"cycle_id": "cycle"},
        TransientProviderError(
            "Groq 503",
            category="provider_server_error",
        ),
    )

    assert transitions[0][2]["fields"]["transient_retry_count"] == 12
    assert worker.provider_blocked_until == pytest.approx(
        before + 2,
        abs=0.1,
    )


@pytest.mark.asyncio
async def test_local_provider_guard_never_persists_or_extends_window(
    monkeypatch,
):
    transitions = []

    async def transition(*args, **kwargs):
        transitions.append((args, kwargs))
        return {}

    monkeypatch.setattr(ia_worker, "transition_cycle", transition)
    worker = worker_without_init()
    worker.provider_blocked_until = time.time() + 60
    original_deadline = worker.provider_blocked_until

    await worker._record_cycle_failure(
        {
            "public_id": "cycle",
            "conversation_id": "ticket",
            "status": "classifying",
            "transient_retry_count": 7,
            "next_attempt_at": "unchanged",
        },
        {"cycle_id": "cycle"},
        ProviderRetryWindowActive(retry_after_seconds=60),
    )

    assert transitions == []
    assert worker.provider_blocked_until == original_deadline


@pytest.mark.asyncio
async def test_active_provider_window_does_not_claim_or_reconcile(
    monkeypatch,
):
    worker = worker_without_init()
    paused = asyncio.Event()

    async def pause(_self):
        paused.set()
        await asyncio.Event().wait()
        return True

    async def unexpected_claim(*args, **kwargs):
        pytest.fail("claim_next_cycle must not run while provider window is active")

    async def unexpected_reconcile(_self):
        pytest.fail("reconciliation must not run while provider window is active")

    monkeypatch.setattr(ia_worker.IAWorker, "_pause_for_provider_window", pause)
    monkeypatch.setattr(ia_worker.IAWorker, "_reconcile_cycles", unexpected_reconcile)
    monkeypatch.setattr(ia_worker, "claim_next_cycle", unexpected_claim)

    task = asyncio.create_task(worker._process_cycles())
    await asyncio.wait_for(paused.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task



@pytest.mark.asyncio
async def test_terminal_audio_blocks_cycle_before_classification(monkeypatch):
    transitions = []

    async def get_states(_audio_ids, _image_ids):
        return {
            "audio": {
                "kind": "audio",
                "status": "failed",
                "attempt_count": 3,
                "text": None,
            }
        }

    async def ensure_media_jobs(_conversation_id, _messages, _states):
        return None

    async def transition(cycle_id, status, **kwargs):
        transitions.append((cycle_id, status, kwargs))
        return {}

    async def unexpected_classification(*_args, **_kwargs):
        pytest.fail("terminal audio must block before Groq classification")

    async def unexpected_persistence(*_args, **_kwargs):
        pytest.fail("terminal audio must not persist a classification")

    monkeypatch.setattr(ia_worker, "get_content_states", get_states)
    monkeypatch.setattr(ia_worker, "transition_cycle", transition)
    monkeypatch.setattr(ia_worker, "insert_classification", unexpected_persistence)
    monkeypatch.setattr(
        ia_worker, "create_request_for_cycle", unexpected_persistence
    )
    worker = worker_without_init()
    worker.max_retries = 3
    worker._ensure_media_jobs = ensure_media_jobs
    worker._analyze_with_groq = unexpected_classification

    await worker._process_cycle(
        {
            "public_id": "cycle-audio-blocked",
            "conversation_id": "ticket-audio-blocked",
            "snapshot_json": {
                "history_recovery": {"complete": True},
                "messages": [
                    {"message_id": "audio", "type": "ptt", "content": ""}
                ],
                "warnings": [],
                "departments": [],
                "agents": [],
            },
        },
        {},
    )

    assert len(transitions) == 1
    assert transitions[0][1] == "media_blocked"
    fields = transitions[0][2]["fields"]
    assert "audio:audio" in fields["error_message"]
    assert fields["snapshot_json"]["media_wait"]["blocked"] is True


@pytest.mark.asyncio
async def test_pending_audio_keeps_cycle_waiting_before_classification(monkeypatch):
    transitions = []

    async def get_states(_audio_ids, _image_ids):
        return {
            "audio": {
                "kind": "audio",
                "status": "pending",
                "attempt_count": 1,
                "next_attempt_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
            }
        }

    async def ensure_media_jobs(_conversation_id, _messages, _states):
        return None

    async def transition(cycle_id, status, **kwargs):
        transitions.append((cycle_id, status, kwargs))
        return {}

    async def unexpected(*_args, **_kwargs):
        pytest.fail("pending audio must not reach classification or persistence")

    monkeypatch.setattr(ia_worker, "get_content_states", get_states)
    monkeypatch.setattr(ia_worker, "transition_cycle", transition)
    monkeypatch.setattr(ia_worker, "insert_classification", unexpected)
    worker = worker_without_init()
    worker.max_retries = 3
    worker._ensure_media_jobs = ensure_media_jobs
    worker._analyze_with_groq = unexpected

    await worker._process_cycle(
        {
            "public_id": "cycle-audio-pending",
            "conversation_id": "ticket-audio-pending",
            "snapshot_json": {
                "history_recovery": {"complete": True},
                "messages": [
                    {"message_id": "audio", "type": "ptt", "content": ""}
                ],
                "warnings": [],
                "departments": [],
                "agents": [],
            },
        },
        {},
    )

    assert len(transitions) == 1
    assert transitions[0][1] == "waiting_media"
    fields = transitions[0][2]["fields"]
    assert fields["next_attempt_at"] is not None
    assert fields["snapshot_json"]["media_wait"]["blocked"] is False


@pytest.mark.asyncio
async def test_postgresql_polling_claims_and_processes_due_cycle(monkeypatch):
    worker = worker_without_init()
    worker.provider_blocked_until = time.time() - 1
    worker.provider_window_resume_pending = True
    processed = asyncio.Event()
    claims = []

    async def reconcile(_self):
        return None

    async def claim(*args, **kwargs):
        claims.append((args, kwargs))
        return {
            "public_id": "cycle",
            "conversation_id": "ticket",
            "status": "classifying",
        }

    async def process(_self, cycle, job):
        processed.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ia_worker.IAWorker, "_reconcile_cycles", reconcile)
    monkeypatch.setattr(ia_worker.IAWorker, "_process_cycle", process)
    monkeypatch.setattr(ia_worker, "claim_next_cycle", claim)

    task = asyncio.create_task(worker._process_cycles())
    await asyncio.wait_for(processed.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(claims) == 1
    assert claims[0][1]["lease_seconds"] == settings.finalization_lease_seconds
    assert worker.provider_window_resume_pending is False


@pytest.mark.asyncio
async def test_restart_after_provider_window_claims_once_without_queue_state(monkeypatch):
    blocked_worker = worker_without_init()
    paused = asyncio.Event()

    async def pause(_self):
        paused.set()
        await asyncio.Event().wait()
        return True

    monkeypatch.setattr(ia_worker.IAWorker, "_pause_for_provider_window", pause)
    blocked_task = asyncio.create_task(blocked_worker._process_cycles())
    await asyncio.wait_for(paused.wait(), timeout=1)
    blocked_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await blocked_task

    restarted_worker = worker_without_init()
    processed = asyncio.Event()
    claims = []

    async def unblocked_pause(_self):
        return False

    async def reconcile(_self):
        return None

    async def claim(*args, **kwargs):
        claims.append((args, kwargs))
        return {
            "public_id": "cycle",
            "conversation_id": "ticket",
            "status": "classifying",
        }

    async def process(_self, cycle, job):
        processed.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ia_worker.IAWorker, "_process_cycle", process)
    monkeypatch.setattr(ia_worker.IAWorker, "_pause_for_provider_window", unblocked_pause)
    monkeypatch.setattr(ia_worker.IAWorker, "_reconcile_cycles", reconcile)
    monkeypatch.setattr(ia_worker, "claim_next_cycle", claim)

    restarted_task = asyncio.create_task(restarted_worker._process_cycles())
    await asyncio.wait_for(processed.wait(), timeout=1)
    restarted_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await restarted_task

    assert len(claims) == 1


def test_media_check_uses_real_future_schedule(monkeypatch):
    worker = worker_without_init()
    future = datetime.fromtimestamp(time.time() + 600, tz=timezone.utc)
    check = worker._next_media_check_at(
        {"image"},
        {
            "image": {
                "status": "pending",
                "attempt_count": 9,
                "next_attempt_at": future.isoformat(),
            }
        },
    )
    assert check == future
