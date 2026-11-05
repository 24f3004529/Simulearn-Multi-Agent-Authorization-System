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
