"""
Looks up the synthetic policy rules store for a policy whose procedure codes
overlap the request's, restricted to the same line of business.
"""

from __future__ import annotations

from failure_injector import FailureInjector
from pa_types import AgentCard, PolicyMatch
from synthetic_data import match_policy

POLICY_MATCHING_CARD = AgentCard(
    agent_name="policy-matching",
    capabilities=["policy_matching"],
    input_schema={
        "procedure_codes": "list[str]",
        "plan_id": "str",
        "line_of_business": "str",
    },
    output_schema={"match": "PolicyMatch"},
)


def policy_matching_agent(
    procedure_codes: list[str],
    plan_id: str,
    line_of_business: str,
    request_id: str,
    failures: FailureInjector,
) -> PolicyMatch | None:
    # Returns None (not an exception) when no policy matches — a real,
    # expected outcome the Orchestrator turns into a PEND.
    failures.maybe_fail("policy-matching")
    matched = match_policy(procedure_codes, line_of_business)
    if matched is None:
        return None
    return PolicyMatch(
        request_id=request_id,
        matched_policy_id=matched["policy_id"],
        coverage_criteria=matched["coverage_criteria"],
        required_docs_list=matched["required_docs_list"],
    )


# DIY Part 2 — in-process twin of policy_matching_runtime/server.py's
# handle_clarification_request, used by the local (non --real) pipeline.
CLARIFICATION_ALLOWED_KEYS = {
    "clarification_request",
    "required_docs_list",
    "missing_documents",
    "request_id",
}


def handle_clarification_request(scoped_input: dict) -> dict:
    """Returns only the items of required_docs_list that correspond to the
    evidence gap — never the full list. Rejects any out-of-scope field."""
    leaked = set(scoped_input) - CLARIFICATION_ALLOWED_KEYS
    if leaked:
        raise KeyError(
            f"clarification_request carries fields outside its scope: {sorted(leaked)}"
        )
    gap = set(scoped_input["missing_documents"])
    missing_documents: list[str] = []
    for doc in scoped_input["required_docs_list"]:
        if doc in gap and doc not in missing_documents:
            missing_documents.append(doc)
    return {
        "request_id": scoped_input.get("request_id", ""),
        "responded_by": POLICY_MATCHING_CARD.agent_name,
        "missing_documents": missing_documents,
    }
