import asyncio
import json
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


class QueueProbeRedis:
    def __init__(self, worker, *, activate_during_lmove=False):
        self.worker = worker
        self.activate_during_lmove = activate_during_lmove
        self.lmove_calls = 0
        self.queue = [
            json.dumps(
                {
                    "cycle_id": "cycle",
                    "conversation_id": "ticket",
                }
            )
        ]
        self.requeued = asyncio.Event()

    async def lpop(self, _name):
        self.lmove_calls += 1
        if not self.queue:
            return None
        item = self.queue.pop(0)
        if self.activate_during_lmove:
            self.worker.provider_blocked_until = time.time() + 60
        return item

    async def rpush(self, name, item):
        self.queue.append(item)
        self.requeued.set()
        return 1


@pytest.mark.asyncio
async def test_active_provider_window_does_not_touch_queue_or_database(
    monkeypatch,
):
    worker = worker_without_init()
    redis = QueueProbeRedis(worker)
    worker.redis = redis
    worker.provider_blocked_until = time.time() + 60
    reconciled = asyncio.Event()

    async def reconcile(_self):
        reconciled.set()

    async def unexpected_claim(*args, **kwargs):
        pytest.fail("claim_cycle must not run while provider window is active")

    async def unexpected_transition(*args, **kwargs):
        pytest.fail("transition_cycle must not run while provider window is active")

    monkeypatch.setattr(ia_worker.IAWorker, "_reconcile_cycles", reconcile)
    monkeypatch.setattr(ia_worker, "claim_cycle", unexpected_claim)
    monkeypatch.setattr(ia_worker, "transition_cycle", unexpected_transition)

    task = asyncio.create_task(worker._process_cycle_queue())
    await asyncio.wait_for(reconciled.wait(), timeout=1)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert redis.lmove_calls == 0
    assert len(redis.queue) == 1


@pytest.mark.asyncio
async def test_provider_window_is_checked_again_before_claim(monkeypatch):
    worker = worker_without_init()
    redis = QueueProbeRedis(worker, activate_during_lmove=True)
    worker.redis = redis

    async def reconcile(_self):
        return None

    async def unexpected_claim(*args, **kwargs):
        pytest.fail("claim_cycle must not run after the provider window opens")

    monkeypatch.setattr(ia_worker.IAWorker, "_reconcile_cycles", reconcile)
    monkeypatch.setattr(ia_worker, "claim_cycle", unexpected_claim)

    task = asyncio.create_task(worker._process_cycle_queue())
    await asyncio.wait_for(redis.requeued.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert redis.lmove_calls == 1
    assert len(redis.queue) == 1


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
async def test_consumption_resumes_after_provider_window_expires(monkeypatch):
    worker = worker_without_init()
    redis = QueueProbeRedis(worker)
    worker.redis = redis
    worker.provider_blocked_until = time.time() - 1
    worker.provider_window_resume_pending = True
    processed = asyncio.Event()

    async def reconcile(_self):
        return None

    async def claim(*args, **kwargs):
        return {
            "public_id": "cycle",
            "conversation_id": "ticket",
            "status": "classifying",
        }

    async def process(_self, cycle, job):
        processed.set()

    monkeypatch.setattr(ia_worker.IAWorker, "_reconcile_cycles", reconcile)
    monkeypatch.setattr(ia_worker.IAWorker, "_process_cycle", process)
    monkeypatch.setattr(ia_worker, "claim_cycle", claim)

    task = asyncio.create_task(worker._process_cycle_queue())
    await asyncio.wait_for(processed.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert redis.lmove_calls >= 1
    assert worker.provider_window_resume_pending is False


@pytest.mark.asyncio
async def test_restart_during_provider_window_preserves_cycle_once(monkeypatch):
    blocked_worker = worker_without_init()
    redis = QueueProbeRedis(blocked_worker)
    blocked_worker.redis = redis
    blocked_worker.provider_blocked_until = time.time() + 60
    reconciled = asyncio.Event()

    async def reconcile(_self):
        reconciled.set()

    monkeypatch.setattr(ia_worker.IAWorker, "_reconcile_cycles", reconcile)
    blocked_task = asyncio.create_task(blocked_worker._process_cycle_queue())
    await asyncio.wait_for(reconciled.wait(), timeout=1)
    await asyncio.sleep(0)
    blocked_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await blocked_task

    assert len(redis.queue) == 1

    restarted_worker = worker_without_init()
    restarted_worker.redis = redis
    redis.worker = restarted_worker
    processed = asyncio.Event()

    async def claim(*args, **kwargs):
        return {
            "public_id": "cycle",
            "conversation_id": "ticket",
            "status": "classifying",
        }

    async def process(_self, cycle, job):
        processed.set()

    monkeypatch.setattr(ia_worker.IAWorker, "_process_cycle", process)
    monkeypatch.setattr(ia_worker, "claim_cycle", claim)

    restarted_task = asyncio.create_task(
        restarted_worker._process_cycle_queue()
    )
    await asyncio.wait_for(processed.wait(), timeout=1)
    restarted_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await restarted_task

    assert redis.queue == []


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
