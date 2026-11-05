"""
Container-local subset of ../../pa_types.py — a deployed Runtime is a
separate process and cannot import across that boundary.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentCard:
    agent_name: str
    capabilities: list[str]
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    version: str = "1.0"


@dataclass
class PolicyMatch:
    request_id: str
    matched_policy_id: str
    coverage_criteria: list[str]
    required_docs_list: list[str]
