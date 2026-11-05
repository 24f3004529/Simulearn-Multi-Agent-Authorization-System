"""
A2A server for the Clinical Extraction agent, deployed via direct code
deployment.
"""

from __future__ import annotations

from a2a_server_boilerplate import run_server
from agent_types import AgentCard, ClinicalSummary, PARequest
from failure_injector import FailureInjector, TransientAgentError

_failures = FailureInjector()

AGENT_CARD = AgentCard(
    agent_name="clinical-extraction",
    capabilities=["clinical_extraction"],
    input_schema={"request": "PARequest (full)"},
    output_schema={"summary": "ClinicalSummary"},
)

DESCRIPTION = (
    "Given the full PA request, extracts a compressed clinical summary "
    "(icd10_codes, cpt_codes, notes_summary)."
)


def agent_function(request: PARequest, failures: FailureInjector) -> ClinicalSummary:
    failures.maybe_fail("clinical-extraction")
    word_count = len(request.clinical_notes.split())
    notes_summary = (
        f"{word_count}-word note covering diagnosis codes "
        f"{', '.join(request.diagnosis_codes)} and procedure codes "
        f"{', '.join(request.procedure_codes)}."
    )
    return ClinicalSummary(
        request_id=request.request_id,
        icd10_codes=request.diagnosis_codes,
        cpt_codes=request.procedure_codes,
        notes_summary=notes_summary,
    )


def handle_request(scoped_input: dict):
    request = PARequest(
        request_id=scoped_input["request_id"],
        procedure_codes=scoped_input["procedure_codes"],
        diagnosis_codes=scoped_input["diagnosis_codes"],
        clinical_notes=scoped_input["clinical_notes"],
    )
    return agent_function(request, _failures)

'''

def agent_function(request: PARequest, failures: FailureInjector) -> ClinicalSummary:
    # TODO: copy your working clinical_extraction_agent() logic here.
    raise NotImplementedError


def handle_request(scoped_input: dict):
    # TODO: build a PARequest from scoped_input and call agent_function().
    raise NotImplementedError

'''

if __name__ == "__main__":
    run_server(
        AGENT_CARD,
        DESCRIPTION,
        handle_request,
        validation_error_cls=KeyError,
        agent_error_cls=TransientAgentError,
    )
