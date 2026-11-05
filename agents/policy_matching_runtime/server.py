"""
A2A server for the Policy Matching agent, deployed via direct code deployment
(no Docker/ECR). Uses only the Python standard library so nothing needs to be
pre-installed or cross-compiled.
"""

from __future__ import annotations

from a2a_server_boilerplate import run_server
from agent_types import AgentCard, PolicyMatch
from failure_injector import FailureInjector, TransientAgentError
from synthetic_data import match_policy

_failures = FailureInjector()

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

DESCRIPTION = (
    "Given procedure codes, plan ID, and line of business, looks up the "
    "synthetic policy rules store and returns a PolicyMatch, or null if no "
    "policy matches."
)


def policy_matching_agent(
    procedure_codes: list[str],
    plan_id: str,
    line_of_business: str,
    request_id: str,
    failures: FailureInjector,
):
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


# DIY Part 2: the only keys a peer clarification request may carry. Anything
# else (clinical notes, notes summary, demographics, coverage criteria, ...)
# is rejected, so context scoping holds even though the caller is a peer
# agent rather than the Orchestrator.
CLARIFICATION_ALLOWED_KEYS = {
    "clarification_request",
    "required_docs_list",
    "missing_documents",
    "request_id",
}


def handle_clarification_request(scoped_input: dict) -> dict:
    """Answers Medical Necessity's peer question: which items of the required
    documents list correspond to the evidence gap it identified. Returns only
    that subset (in required-list order, de-duplicated), never the full list."""
    leaked = set(scoped_input) - CLARIFICATION_ALLOWED_KEYS
    if leaked:
        # KeyError -> JSON-RPC validation error (-32052) via run_server.
        raise KeyError(
            f"clarification_request carries fields outside its scope: {sorted(leaked)}"
        )
    required_docs_list = list(scoped_input["required_docs_list"])
    gap = set(scoped_input["missing_documents"])
    missing_documents: list[str] = []
    for doc in required_docs_list:
        if doc in gap and doc not in missing_documents:
            missing_documents.append(doc)
    print(
        f"[policy-matching] clarification_request request_id="
        f"{scoped_input.get('request_id', '')!r} payload_keys={sorted(scoped_input)} "
        f"-> missing_documents={missing_documents}",
        flush=True,
    )
    return {
        "request_id": scoped_input.get("request_id", ""),
        "responded_by": POLICY_MATCHING_CARD.agent_name,
        "missing_documents": missing_documents,
    }


def handle_request(scoped_input: dict):
    if scoped_input.get("clarification_request"):
        return handle_clarification_request(scoped_input)
    procedure_codes = scoped_input["procedure_codes"]
    plan_id = scoped_input["plan_id"]
    line_of_business = scoped_input["line_of_business"]
    request_id = scoped_input.get("request_id", "")
    return policy_matching_agent(
        procedure_codes=procedure_codes,
        plan_id=plan_id,
        line_of_business=line_of_business,
        request_id=request_id,
        failures=_failures,
    )


if __name__ == "__main__":
    run_server(
        POLICY_MATCHING_CARD,
        DESCRIPTION,
        handle_request,
        validation_error_cls=KeyError,
        agent_error_cls=TransientAgentError,
    )
