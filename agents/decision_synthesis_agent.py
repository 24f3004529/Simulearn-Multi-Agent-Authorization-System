"""
Turns a medical necessity determination and matched policy into the final
approve/deny/pend decision. Last agent in the pipeline before COMPLETE.
"""

from __future__ import annotations

from failure_injector import FailureInjector
from pa_types import AgentCard, Decision, FinalDetermination, NecessityResult

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


def decision_synthesis_agent(
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
            rationale=(
                f"Approved under policy {policy_id}: {'; '.join(evidence_citations)}"
            ),
            next_actions=[],
        )

    if necessity_result == NecessityResult.NOT_MET.value:
        return FinalDetermination(
            request_id=request_id,
            decision=Decision.DENY,
            rationale=(
                f"Denied under policy {policy_id}: {'; '.join(evidence_citations)}"
            ),
            next_actions=["Submit an appeal with additional clinical justification."],
        )

    # INSUFFICIENT_EVIDENCE — handled defensively; the orchestrator normally
    # routes this to PEND before decision synthesis is ever called.
    return FinalDetermination(
        request_id=request_id,
        decision=Decision.PEND,
        rationale=f"Pending under policy {policy_id}: insufficient evidence.",
        next_actions=[f"Submit: {doc}" for doc in missing_evidence],
    )

'''


def decision_synthesis_agent(
    necessity_result: str,
    evidence_citations: list[str],
    missing_evidence: list[str],
    policy_id: str,
    request_id: str,
    failures: FailureInjector,
) -> FinalDetermination:
    # TODO 1: call failures.maybe_fail("decision-synthesis") first.
    # TODO 2: map necessity_result to a Decision — MET -> APPROVE, NOT_MET ->
    # DENY, INSUFFICIENT_EVIDENCE -> PEND (handle this case defensively even
    # though the Orchestrator normally routes it to PEND before reaching here).
    # TODO 3: write a rationale citing policy_id and, for a deny, the relevant
    # evidence_citations.
    # TODO 4: populate next_actions — empty for approve, an appeal note for
    # deny, the specific missing_evidence to submit for pend.
    # TODO 5: return a FinalDetermination with request_id, decision, rationale,
    # next_actions populated.
    raise NotImplementedError
'''