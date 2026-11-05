"""
Container-local copy of ../../failure_injector.py, bundled with
TransientAgentError since this container has no access to ../../recovery.py.
The circuit breaker itself lives in orchestrator.py, outside this container.
"""

from __future__ import annotations


class TransientAgentError(Exception):
    pass


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
