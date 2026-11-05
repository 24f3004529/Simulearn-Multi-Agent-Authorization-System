"""
Retry with backoff, per-agent circuit breaker, dead-letter queue, and
manual-review queue. One circuit breaker per agent, not a shared one — a
shared breaker would let failures in one agent block calls to a healthy one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, TypeVar

T = TypeVar("T")


class TransientAgentError(Exception):
    """A transient failure (timeout, incomplete record) that retry/backoff and
    the circuit breaker are meant to handle."""


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Opens after failure_threshold consecutive failures; after
    cooldown_seconds it allows one probe call (half-open) before closing."""

    failure_threshold: int = 3
    cooldown_seconds: float = 30.0

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    @property
    def state(self) -> CircuitState:
        # Read-only; does not evaluate the open->half-open cooldown transition.
        # Call refresh_state() explicitly for that.
        return self._state

    def refresh_state(self) -> CircuitState:
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    def allow_call(self) -> bool:
        return self.refresh_state() != CircuitState.OPEN


class DeadLetterQueue:
    """Requests that exhausted all recovery options — nothing is silently dropped."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def add(self, request_id: str, agent_name: str, reason: str) -> None:
        self.entries.append(
            {"request_id": request_id, "agent_name": agent_name, "reason": reason}
        )

    def __len__(self) -> int:
        return len(self.entries)


class ManualReviewQueue:
    """Graceful-degradation destination when an agent's circuit is open."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def add(self, request_id: str, agent_name: str, reason: str) -> None:
        self.entries.append(
            {"request_id": request_id, "agent_name": agent_name, "reason": reason}
        )

    def __len__(self) -> int:
        return len(self.entries)


class RecoveryExhaustedError(Exception):
    """Raised when retries and the circuit breaker both fail to produce a
    result — the caller decides between manual review and dead-lettering."""


def call_with_recovery(
    fn: Callable[[], T],
    *,
    agent_name: str,
    breaker: CircuitBreaker,
    max_retries: int = 2,
    base_backoff_seconds: float = 0.0,
) -> T:
    if not breaker.allow_call():
        raise RecoveryExhaustedError(
            f"{agent_name}: circuit breaker is open, call skipped"
        )

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            result = fn()
            breaker.record_success()
            return result
        except TransientAgentError as e:
            last_error = e
            breaker.record_failure()
            if attempt < max_retries:
                time.sleep(base_backoff_seconds * (2**attempt))
                continue
    raise RecoveryExhaustedError(
        f"{agent_name}: exhausted {max_retries} retries - last error: {last_error}"
    )
