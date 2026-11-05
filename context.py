"""
Context scoping layer. Each scope_for_* function returns only the fields the
target agent is allowed to see; assert_isolation checks a scoped payload for
forbidden keys and raises if scoping was violated.
"""

from __future__ import annotations

from typing import Any

from pa_types import PARequest


class IsolationViolationError(Exception):
    pass


def scope_for_policy_matching(request: PARequest) -> dict[str, Any]:
    """Policy Matching receives only procedure codes, plan ID, and line of
    business — no clinical notes or patient demographics."""
    return {
        "procedure_codes": list(request.procedure_codes),
        "plan_id": request.plan_id,
        "line_of_business": request.line_of_business,
    }


def scope_for_medical_necessity(
    notes_summary: str, coverage_criteria: list[str], required_docs_list: list[str]
) -> dict[str, Any]:
    """Medical Necessity receives only the clinical summary and matched policy
    criteria — no plan details, billing info, or subscriber ID."""
    return {
        "notes_summary": notes_summary,
        "coverage_criteria": list(coverage_criteria),
        "required_docs_list": list(required_docs_list),
    }


def scope_for_decision_synthesis(
    necessity_result: str,
    evidence_citations: list[str],
    missing_evidence: list[str],
    policy_id: str,
) -> dict[str, Any]:
    """Decision Synthesis receives compressed summaries only — no raw clinical
    notes, raw policy text, or billing info."""
    return {
        "necessity_result": necessity_result,
        "evidence_citations": list(evidence_citations),
        "missing_evidence": list(missing_evidence),
        "policy_id": policy_id,
    }


FORBIDDEN_FOR_POLICY_MATCHING = {
    "clinical_notes",
    "patient_name",
    "patient_dob",
    "diagnosis_codes",
}
FORBIDDEN_FOR_MEDICAL_NECESSITY = {"plan_id", "billing_info", "subscriber_id"}
FORBIDDEN_FOR_DECISION_SYNTHESIS = {"clinical_notes", "billing_info", "raw_policy_text"}


def assert_isolation(
    payload: dict[str, Any], forbidden_keys: set[str], *, agent_name: str
) -> None:
    leaked = forbidden_keys & payload.keys()
    if leaked:
        raise IsolationViolationError(
            f"Context isolation violated for '{agent_name}': leaked fields {sorted(leaked)}"
        )
