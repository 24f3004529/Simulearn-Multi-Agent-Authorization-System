"""
Container-local subset of ../../pa_types.py. This PARequest is deliberately
trimmed — only the fields Clinical Extraction's contract documents. A field
read here that isn't defined below will work locally (main.py imports the
real, full PARequest) but fail once this Runtime is deployed.
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
class PARequest:
    request_id: str
    procedure_codes: list[str]
    diagnosis_codes: list[str]
    clinical_notes: str


@dataclass
class ClinicalSummary:
    request_id: str
    icd10_codes: list[str]
    cpt_codes: list[str]
    notes_summary: str
