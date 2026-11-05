"""
A2A server for the Medical Necessity agent, deployed via direct code
deployment.
"""

from __future__ import annotations

import boto3
from a2a_server_boilerplate import run_server
from agent_types import AgentCard, NecessityDetermination, NecessityResult
from failure_injector import FailureInjector, TransientAgentError
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
        # TODO(DIY Part 2): call peer_client.request_clarification() here and
        # set peer_clarification on the returned NecessityDetermination.
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
