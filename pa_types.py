"""
Shared dataclasses and enums for prior authorization requests, agent contracts,
and pipeline state. Every agent imports its input/output types from here
rather than redefining them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PAState(str, Enum):
    INTAKE = "INTAKE"
    EXTRACTION = "EXTRACTION"
    POLICY_MATCH = "POLICY_MATCH"
    MED_NECESSITY = "MED_NECESSITY"
    DETERMINATION = "DETERMINATION"
    PEND = "PEND"
    COMPLETE = "COMPLETE"
    DEAD_LETTER = "DEAD_LETTER"


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
    """Published by each agent and resolved by the Orchestrator via capability
    lookup, never a hardcoded reference."""

    agent_name: str
    capabilities: list[str]
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    version: str = "1.0"


@dataclass
class PARequest:
    """The only payload that ever carries full patient/billing context — every
    downstream agent receives a scoped subset instead."""

    request_id: str
    patient_name: str
    patient_dob: str
    subscriber_id: str
    plan_id: str
    line_of_business: str
    procedure_codes: list[str]
    diagnosis_codes: list[str]
    clinical_notes: str
    billing_info: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClinicalSummary:
    """Output of the Clinical Extraction agent."""

    request_id: str
    icd10_codes: list[str]
    cpt_codes: list[str]
    notes_summary: str


@dataclass
class PolicyMatch:
    """Output of the Policy Matching agent."""

    request_id: str
    matched_policy_id: str
    coverage_criteria: list[str]
    required_docs_list: list[str]


@dataclass
class NecessityDetermination:
    """Output of the Medical Necessity agent."""

    request_id: str
    result: NecessityResult
    evidence_citations: list[str]
    missing_evidence: list[str] = field(default_factory=list)
    # TODO(DIY Part 2): add peer_clarification: dict | None = None here —
    # must match agents/medical_necessity_runtime/agent_types.py's copy.
    # Edit this existing class in place; don't add a second
    # NecessityDetermination definition elsewhere in this file — Python
    # silently uses whichever one is defined last, so a second copy would
    # shadow this fix instead of applying it.


@dataclass
class FinalDetermination:
    """Output of the Decision Synthesis agent."""

    request_id: str
    decision: Decision
    rationale: str
    next_actions: list[str] = field(default_factory=list)


def new_correlation_id() -> str:
    """Correlation ID for the A2A request/response envelope."""
    return str(uuid.uuid4())
