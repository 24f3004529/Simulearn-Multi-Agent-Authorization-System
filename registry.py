"""
Agent card registry with capability-based discovery. Agents register a card
once; every caller resolves peers by capability, never by a hardcoded name or
endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pa_types import AgentCard


class AgentNotFoundError(Exception):
    pass


class AgentVersionMismatchError(Exception):
    pass


@dataclass
class RegisteredAgent:
    card: AgentCard
    invoke_fn: Callable


class AgentCardRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, RegisteredAgent] = {}

    def register(self, card: AgentCard, invoke_fn: Callable) -> None:
        # TODO(DIY Part 1): also write `card` to AgentCardRegistryTable here.
        self._agents[card.agent_name] = RegisteredAgent(card=card, invoke_fn=invoke_fn)

    def discover_by_capability(
        self, capability: str, min_version: str = "1.0"
    ) -> RegisteredAgent:
        # TODO(DIY Part 1): read candidates from AgentCardRegistryTable
        # instead of self._agents.
        # Resolves the first agent advertising `capability` at or above
        # `min_version`; raises if none found or only a lower version matches.
        candidates = [
            a for a in self._agents.values() if capability in a.card.capabilities
        ]
        if not candidates:
            raise AgentNotFoundError(
                f"No registered agent advertises capability '{capability}'"
            )

        for agent in candidates:
            if _version_at_least(agent.card.version, min_version):
                return agent

        raise AgentVersionMismatchError(
            f"Agent(s) advertising '{capability}' found, but none satisfy min_version={min_version} "
            f"(found versions: {[a.card.version for a in candidates]})"
        )

    def is_available(self, agent_name: str) -> bool:
        # TODO(DIY Part 1): read from AgentCardRegistryTable instead of
        # self._agents.
        return agent_name in self._agents

    def get_card(self, agent_name: str) -> AgentCard:
        # TODO(DIY Part 1): read from AgentCardRegistryTable instead of
        # self._agents.
        if agent_name not in self._agents:
            raise AgentNotFoundError(f"No registered agent named '{agent_name}'")
        return self._agents[agent_name].card


def _version_at_least(actual: str, minimum: str) -> bool:
    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(p) for p in v.split("."))

    return parts(actual) >= parts(minimum)
