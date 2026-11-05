"""
A2A server for the Medical Necessity agent, deployed via direct code
deployment.
"""

from __future__ import annotations

import boto3
from a2a_server_boilerplate import run_server
from agent_types import AgentCard, NecessityDetermination, NecessityResult
from failure_injector import FailureInjector, TransientAgentError
from peer_client import (PEER_AGENT_NAME, RESOLVED_VIA, PeerUnavailableError,
                         request_clarification)
from synthetic_data import ATTACHED_DOCUMENTS

_failures = FailureInjector()

AGENT_CARD = AgentCard(
    agent_name="medical-necessity",
    capabilities=["medical_necessity"],
    input_schema={
        "notes_summary": "str",
        "coverage_criteria": "list[str]",
        "required_docs_list": "list[str]",
    },
    output_schema={"determination": "NecessityDetermination"},
)

DESCRIPTION = (
    "Given a clinical summary and matched policy criteria, determines whether "
    "medical necessity evidence is met, not met, or insufficient."
)

# Cross-region inference profile ID (US CRIS endpoint) — required for Nova
# models rather than a plain foundation-model ID. The execution role already
# has bedrock:InvokeModel, which Converse also uses.
_MODEL_ID = "us.amazon.nova-2-lite-v1:0"


def _judge_necessity(notes_summary: str, coverage_criteria: list[str]) -> NecessityResult:
    """Asks Nova 2 Lite whether the clinical summary satisfies the coverage
    criteria, expecting exactly one word back. Any answer that isn't a
    clean "met" match falls back to NOT_MET rather than guessing."""
    criteria_text = (
        "; ".join(coverage_criteria) if coverage_criteria else "No specific criteria required."
    )
    prompt = (
        "You are reviewing a prior authorization request for medical necessity. "
        f"Clinical summary: {notes_summary}\n"
        f"Coverage criteria that must be satisfied: {criteria_text}\n"
        "Does the clinical summary satisfy the coverage criteria? "
        "Answer with exactly one word: met or not_met."
    )

    client = boto3.client("bedrock-runtime")
    response = client.converse(
        modelId=_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 10, "temperature": 0},
    )
    answer = response["output"]["message"]["content"][0]["text"].strip().lower()
    return NecessityResult.MET if answer.startswith("met") else NecessityResult.NOT_MET


# FR-4: Met / Not Met never trigger a peer call.
_NO_PEER_CLARIFICATION = {
    "requested": False,
    "peer_agent": None,
    "resolved_via": None,
    "missing_documents": None,
}


def _clarify_missing_documents(
    required_docs_list: list[str], missing_documents: list[str], request_id: str
) -> tuple[list[str], dict]:
    """FR-1/FR-2: exactly one peer call to Policy Matching for this request.
    Returns (missing_evidence, peer_clarification). On any peer failure,
    falls back to the locally computed list (generic PEND) without failing
    the request; the attempt is still reported so it can be audited and
    told apart from "no clarification needed"."""
    try:
        confirmed = request_clarification(
            required_docs_list=required_docs_list,
            missing_documents=missing_documents,
            request_id=request_id,
        )
    except PeerUnavailableError as exc:
        return missing_documents, {
            "requested": True,
            "status": "failed",
            "initiated_by": "agent:medical-necessity",
            "peer_agent": PEER_AGENT_NAME,
            "resolved_via": RESOLVED_VIA,
            "missing_documents": None,
            "error": str(exc),
        }
    return confirmed, {
        "requested": True,
        "status": "resolved",
        "initiated_by": "agent:medical-necessity",
        "peer_agent": PEER_AGENT_NAME,
        "resolved_via": RESOLVED_VIA,
        "missing_documents": confirmed,
    }


def agent_function(
    notes_summary: str,
    coverage_criteria: list[str],
    required_docs_list: list[str],
    request_id: str,
    failures: FailureInjector,
) -> NecessityDetermination:
    failures.maybe_fail("medical-necessity")

    attached_documents = ATTACHED_DOCUMENTS.get(request_id, set())
    missing_documents = [
        doc for doc in required_docs_list if doc not in attached_documents
    ]

    if missing_documents:
        missing_evidence, peer_clarification = _clarify_missing_documents(
            required_docs_list, missing_documents, request_id
        )
        return NecessityDetermination(
            request_id=request_id,
            result=NecessityResult.INSUFFICIENT_EVIDENCE,
            evidence_citations=[
                (
                    f"Coverage criteria: {', '.join(coverage_criteria)}"
                    if coverage_criteria
                    else "No coverage criteria required."
                ),
                f"Clinical summary: {notes_summary}",
            ],
            missing_evidence=missing_evidence,
            peer_clarification=peer_clarification,
        )

    result = _judge_necessity(notes_summary, coverage_criteria)
    return NecessityDetermination(
        request_id=request_id,
        result=result,
        evidence_citations=[
            f"All required documentation present: "
            f"{', '.join(required_docs_list) if required_docs_list else 'none required'}.",
            f"Clinical summary: {notes_summary}",
            f"Necessity judgment (Nova 2 Lite): {result.value}",
        ],
        missing_evidence=[],
        peer_clarification=dict(_NO_PEER_CLARIFICATION),
    )


def handle_request(scoped_input: dict):
    notes_summary = scoped_input["notes_summary"]
    coverage_criteria = scoped_input["coverage_criteria"]
    required_docs_list = scoped_input["required_docs_list"]
    request_id = scoped_input.get("request_id", "")
    return agent_function(
        notes_summary=notes_summary,
        coverage_criteria=coverage_criteria,
        required_docs_list=required_docs_list,
        request_id=request_id,
        failures=_failures,
    )


if __name__ == "__main__":
    run_server(
        AGENT_CARD,
        DESCRIPTION,
        handle_request,
        validation_error_cls=KeyError,
        agent_error_cls=TransientAgentError,
    )
