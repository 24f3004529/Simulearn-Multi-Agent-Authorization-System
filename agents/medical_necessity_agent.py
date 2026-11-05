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
            missing_evidence=missing_documents,
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
    )
