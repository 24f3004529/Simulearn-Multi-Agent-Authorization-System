"""
Checks whether every required document is on file, then — if complete —
asks Amazon Nova 2 Lite whether the clinical summary satisfies the coverage
criteria. Missing documentation always produces INSUFFICIENT_EVIDENCE
(never a denial); only the met/not_met judgment calls a real model.
"""

from __future__ import annotations

import boto3

from failure_injector import FailureInjector
from pa_types import AgentCard, NecessityDetermination, NecessityResult
from synthetic_data import ATTACHED_DOCUMENTS

_PEER_AGENT_NAME = "policy-matching"
_LOCAL_RESOLVED_VIA = "in-process:agents.policy_matching_agent"

_NO_PEER_CLARIFICATION = {
    "requested": False,
    "peer_agent": None,
    "resolved_via": None,
    "missing_documents": None,
}


def _clarify_missing_documents(
    required_docs_list: list[str], missing_documents: list[str], request_id: str
) -> tuple[list[str], dict]:
    """Local twin of medical_necessity_runtime/server.py's peer call: one
    scoped clarification request to Policy Matching, generic-PEND fallback
    on any failure."""
    from agents.policy_matching_agent import handle_clarification_request

    payload = {
        "clarification_request": True,
        "required_docs_list": list(required_docs_list),
        "missing_documents": list(missing_documents),
        "request_id": request_id,
    }
    try:
        confirmed = handle_clarification_request(payload)["missing_documents"]
        if not confirmed:
            raise ValueError("peer confirmed no missing documents for a known gap")
    except Exception as exc:  # noqa: BLE001 - any peer failure -> generic PEND
        print(f"[peer_client] PeerUnavailableError: {exc}", flush=True)
        return missing_documents, {
            "requested": True,
            "status": "failed",
            "initiated_by": "agent:medical-necessity",
            "peer_agent": _PEER_AGENT_NAME,
            "resolved_via": _LOCAL_RESOLVED_VIA,
            "missing_documents": None,
            "error": str(exc),
        }
    return confirmed, {
        "requested": True,
        "status": "resolved",
        "initiated_by": "agent:medical-necessity",
        "peer_agent": _PEER_AGENT_NAME,
        "resolved_via": _LOCAL_RESOLVED_VIA,
        "missing_documents": confirmed,
    }

MEDICAL_NECESSITY_CARD = AgentCard(
    agent_name="medical-necessity",
    capabilities=["medical_necessity"],
    input_schema={
        "notes_summary": "str",
        "coverage_criteria": "list[str]",
        "required_docs_list": "list[str]",
    },
    output_schema={"determination": "NecessityDetermination"},
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


def medical_necessity_agent(
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
