"""
Minimal shared types needed by this container — a subset of ../../pa_types.py,
copied here because a deployed AgentCore Runtime container is a separate
process and cannot import across that process boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class NecessityResult(str, Enum):
    MET = "met"
    NOT_MET = "not_met"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Decision(str, Enum):
    APPROVE = "approve"
    DENY = "deny"
    PEND = "pend"


@dataclass
class AgentCard:
    agent_name: str
    capabilities: list[str]
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    version: str = "1.0"


@dataclass
class FinalDetermination:
    request_id: str
    decision: Decision
    rationale: str
    next_actions: list[str] = field(default_factory=list)
