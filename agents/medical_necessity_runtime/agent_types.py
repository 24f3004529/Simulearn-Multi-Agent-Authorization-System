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


@dataclass
class AgentCard:
    agent_name: str
    capabilities: list[str]
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    version: str = "1.0"


@dataclass
class NecessityDetermination:
    request_id: str
    result: NecessityResult
    evidence_citations: list[str]
    missing_evidence: list[str] = field(default_factory=list)
    # TODO(DIY Part 2): add peer_clarification: dict | None = None here —
    # must match pa_types.py's NecessityDetermination.
