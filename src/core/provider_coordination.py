"""Transient coordination primitives shared by provider adapters.

This module deliberately knows nothing about a particular provider, request
payload, credential, persistence layer, or application configuration.  Its
state is process-local and exists only to coordinate provider admission.
"""

from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Callable


class SlidingWindowRateLimiter:
    """Serialize admission through a process-local sliding window."""

    _shared_states: dict[str, "_SlidingWindowState"] = {}
    _shared_states_lock = Lock()

    def __init__(
        self,
        limit_per_minute: int,
        *,
        sleep: Callable[[float], None],
        clock: Callable[[], float],
        shared_key: str | None = None,
    ) -> None:
        if not 1 <= limit_per_minute <= 100:
            raise ValueError("provider request rate must be between 1 and 100")
        self.limit = limit_per_minute
        self.sleep = sleep
        self.clock = clock
        if shared_key is None:
            self._state = _SlidingWindowState(limit_per_minute)
        else:
            with self._shared_states_lock:
                state = self._shared_states.get(shared_key)
                if state is None:
                    state = _SlidingWindowState(limit_per_minute)
                    self._shared_states[shared_key] = state
                self._state = state

    def before_request(self) -> None:
        with self._state.lock:
            while True:
                now = self.clock()
                while self._state.requests and now - self._state.requests[0] >= 60:
                    self._state.requests.popleft()
                if len(self._state.requests) < self._state.limit:
                    self._state.requests.append(now)
                    return
                delay = max(0.0, 60 - (now - self._state.requests[0]))
                self.sleep(delay)


class _SlidingWindowState:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.requests: deque[float] = deque()
        self.lock = Lock()
