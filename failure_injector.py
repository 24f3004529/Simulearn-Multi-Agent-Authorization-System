"""
Deterministic, on-demand failure injection. Call force_failures() to make the
next N calls to a named agent raise TransientAgentError, so retry/circuit-
breaker/dead-letter logic can be exercised without waiting for random flakiness.
"""

from __future__ import annotations

from recovery import TransientAgentError


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
