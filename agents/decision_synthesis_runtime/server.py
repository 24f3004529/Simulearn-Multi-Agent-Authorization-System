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
    # TODO: copy your working decision_synthesis_agent() logic here.
    raise NotImplementedError


def handle_request(scoped_input: dict):
    # TODO: pull the scoped fields out of scoped_input and call
    # agent_function().
    raise NotImplementedError


if __name__ == "__main__":
    run_server(
        DECISION_SYNTHESIS_CARD,
        DESCRIPTION,
        handle_request,
        validation_error_cls=KeyError,
        agent_error_cls=TransientAgentError,
    )
