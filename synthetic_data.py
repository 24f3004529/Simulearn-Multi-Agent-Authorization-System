"""
Synthetic policy rules, sample requests, and attached-document state. All
data here is fully synthetic — no real patient, provider, or payer data.
"""

from __future__ import annotations

from pa_types import PARequest

POLICY_RULES_STORE: dict[str, dict] = {
    "POL-LAB-ROUTINE": {
        "line_of_business": "commercial",
        "procedure_codes": ["80053", "85025"],
        "coverage_criteria": [
            "Ordered by in-network provider",
            "No prior denial in last 90 days",
        ],
        "required_docs_list": [],
    },
    "POL-MRI-LUMBAR": {
        "line_of_business": "commercial",
        "procedure_codes": ["72148"],
        "coverage_criteria": [
            "Conservative therapy attempted >= 6 weeks",
            "No red-flag symptoms",
        ],
        "required_docs_list": ["chart_notes_last_90_days", "imaging_report"],
    },
    "POL-SURGERY-KNEE": {
        "line_of_business": "commercial",
        "procedure_codes": ["27447"],
        "coverage_criteria": [
            "Failed conservative treatment",
            "BMI within program guidelines",
            "Imaging confirms joint damage",
        ],
        "required_docs_list": [
            "chart_notes_last_90_days",
            "imaging_report",
            "conservative_treatment_log",
        ],
    },
}


def match_policy(procedure_codes: list[str], line_of_business: str) -> dict | None:
    for policy_id, rule in POLICY_RULES_STORE.items():
        if rule["line_of_business"] != line_of_business:
            continue
        if any(code in rule["procedure_codes"] for code in procedure_codes):
            return {"policy_id": policy_id, **rule}
    return None


SAMPLE_REQUESTS: list[PARequest] = [
    PARequest(
        request_id="PA-0001",
        patient_name="Jordan Alvarez",
        patient_dob="1985-03-14",
        subscriber_id="SUB-10023",
        plan_id="PLN-COMM-01",
        line_of_business="commercial",
        procedure_codes=["80053"],
        diagnosis_codes=["Z00.00"],
        clinical_notes="Routine annual physical, no acute complaints. Ordered comprehensive metabolic panel.",
        billing_info={"copay": 20, "deductible_met": True},
    ),
    PARequest(
        request_id="PA-0002",
        patient_name="Priya Natarajan",
        patient_dob="1978-11-02",
        subscriber_id="SUB-10091",
        plan_id="PLN-COMM-01",
        line_of_business="commercial",
        procedure_codes=["72148"],
        diagnosis_codes=["M54.5"],
        clinical_notes=(
            "Patient reports 8 weeks of lower back pain, no radiculopathy, no red-flag symptoms. "
            "Completed 7 weeks of physical therapy with minimal improvement. Chart notes from "
            "the last 90 days attached. No prior imaging on file."
        ),
        billing_info={"copay": 40, "deductible_met": True},
    ),
    PARequest(
        request_id="PA-0003",
        patient_name="Marcus Webb",
        patient_dob="1962-07-22",
        subscriber_id="SUB-10147",
        plan_id="PLN-COMM-02",
        line_of_business="commercial",
        procedure_codes=["27447"],
        diagnosis_codes=["M17.11"],
        clinical_notes=(
            "Severe right knee osteoarthritis, failed 6 months conservative treatment "
            "(NSAIDs, physical therapy, corticosteroid injections). Requesting total knee "
            "arthroplasty. No imaging report attached yet - pending radiology upload."
        ),
        billing_info={"copay": 150, "deductible_met": False},
    ),
]

# Documents actually attached per request, keyed by request_id.
ATTACHED_DOCUMENTS: dict[str, set[str]] = {
    "PA-0001": set(),
    "PA-0002": {"chart_notes_last_90_days"},
    "PA-0003": {"chart_notes_last_90_days", "conservative_treatment_log"},
}
