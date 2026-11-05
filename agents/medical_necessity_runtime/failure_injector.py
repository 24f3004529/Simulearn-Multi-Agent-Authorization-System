"""
Deterministic, on-demand failure injection — container-local copy of
../../failure_injector.py + the TransientAgentError it depends on from
../../recovery.py. Bundled together here since this container has no access to
the Orchestrator process's recovery.py; the real per-agent circuit breaker now
lives in orchestrator.py and wraps the InvokeAgentRuntime call from outside
this container, not inside it.
"""

from __future__ import annotations


class TransientAgentError(Exception):
    """Raised to simulate a transient failure so the Orchestrator's real
    per-agent circuit breaker (recovery.py, wrapping InvokeAgentRuntime) has
    something to retry against."""


class FailureInjector:
    def __init__(self) -> None:
        self._forced_failures: dict[str, int] = {}

    def force_failures(self, agent_name: str, count: int) -> None:
        self._forced_failures[agent_name] = count

    def maybe_fail(self, agent_name: str) -> None:
        remaining = self._forced_failures.get(agent_name, 0)
        if remaining > 0:
            self._forced_failures[agent_name] = remaining - 1
            raise TransientAgentError(
                f"{agent_name}: injected transient failure ({remaining} remaining)"
            )
