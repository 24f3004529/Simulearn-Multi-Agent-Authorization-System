"""
A2A server for the Decision Synthesis agent, deployed via direct code
deployment.
"""

from __future__ import annotations

from a2a_server_boilerplate import run_server
from agent_types import (AgentCard, Decision, FinalDetermination,
                         NecessityResult)
from failure_injector import FailureInjector, TransientAgentError

_failures = FailureInjector()

DECISION_SYNTHESIS_CARD = AgentCard(
    agent_name="decision-synthesis",
    capabilities=["decision_synthesis"],
    input_schema={
        "necessity_result": "str",
        "evidence_citations": "list[str]",
        "missing_evidence": "list[str]",
        "policy_id": "str",
    },
    output_schema={"determination": "FinalDetermination"},
)

DESCRIPTION = (
    "Given a necessity determination and matched policy ID, synthesizes the "
    "final approve/deny/pend decision with a rationale and next actions."
)


def agent_function(
    necessity_result: str,
    evidence_citations: list[str],
    missing_evidence: list[str],
    policy_id: str,
    request_id: str,
    failures: FailureInjector,
) -> FinalDetermination:
    failures.maybe_fail("decision-synthesis")

    if necessity_result == NecessityResult.MET.value:
        return FinalDetermination(
            request_id=request_id,
            decision=Decision.APPROVE,
            rationale=f"Approved under policy {policy_id}: {'; '.join(evidence_citations)}",
            next_actions=[],
        )
    if necessity_result == NecessityResult.NOT_MET.value:
        return FinalDetermination(
            request_id=request_id,
            decision=Decision.DENY,
            rationale=f"Denied under policy {policy_id}: {'; '.join(evidence_citations)}",
            next_actions=["Submit an appeal with additional clinical justification."],
        )
    return FinalDetermination(
        request_id=request_id,
        decision=Decision.PEND,
        rationale=f"Pending under policy {policy_id}: insufficient evidence.",
        next_actions=[f"Submit: {doc}" for doc in missing_evidence],
    )


def handle_request(scoped_input: dict):
    return agent_function(
        necessity_result=scoped_input["necessity_result"],
        evidence_citations=scoped_input["evidence_citations"],
        missing_evidence=scoped_input["missing_evidence"],
        policy_id=scoped_input["policy_id"],
        request_id=scoped_input.get("request_id", ""),
        failures=_failures,
    )

'''

def agent_function(
    necessity_result: str,
    evidence_citations: list[str],
    missing_evidence: list[str],
    policy_id: str,
    request_id: str,
    failures: FailureInjector,
) -> FinalDetermination:
    # TODO: copy your working decision_synthesis_agent() logic here.
    raise NotImplementedError


def handle_request(scoped_input: dict):
    # TODO: pull the scoped fields out of scoped_input and call
    # agent_function().
    raise NotImplementedError

'''

if __name__ == "__main__":
    run_server(
        DECISION_SYNTHESIS_CARD,
        DESCRIPTION,
        handle_request,
        validation_error_cls=KeyError,
        agent_error_cls=TransientAgentError,
    )
