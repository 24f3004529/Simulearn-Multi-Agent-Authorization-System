"""
Extracts a compressed clinical summary from the full prior-authorization
request. First agent in the pipeline — receives the full request, since
nothing has been scoped away yet.
"""

from __future__ import annotations

from failure_injector import FailureInjector
from pa_types import AgentCard, ClinicalSummary, PARequest

CLINICAL_EXTRACTION_CARD = AgentCard(
    agent_name="clinical-extraction",
    capabilities=["clinical_extraction"],
    input_schema={"request": "PARequest (full)"},
    output_schema={"summary": "ClinicalSummary"},
)


def clinical_extraction_agent(request: PARequest, failures: FailureInjector) -> ClinicalSummary:
    failures.maybe_fail("clinical-extraction")
  
    word_count = len(request.clinical_notes.split())
    copay = request.billing_info.get("copay", "unknown")
    notes_summary = (
        f"{word_count}-word clinical note covering diagnosis code(s) "
        f"{', '.join(request.diagnosis_codes)} and procedure code(s) "
        f"{', '.join(request.procedure_codes)} (copay: {copay})."
    )

    return ClinicalSummary(
        request_id=request.request_id,
        icd10_codes=list(request.diagnosis_codes),
        cpt_codes=list(request.procedure_codes),
        notes_summary=notes_summary,
      )

'''

def clinical_extraction_agent(
    request: PARequest, failures: FailureInjector
) -> ClinicalSummary:
    # TODO 1: call failures.maybe_fail("clinical-extraction") first, before any
    # other logic, so retries can be exercised against this agent.
    # TODO 2: derive notes_summary from request.clinical_notes — a short,
    # deterministic summary is fine (e.g. word count plus the diagnosis/
    # procedure codes it covers); this does not need real NLP.
    # TODO 3: return a ClinicalSummary with request_id, icd10_codes
    # (request.diagnosis_codes), cpt_codes (request.procedure_codes), and
    # notes_summary populated.
    raise NotImplementedError
'''