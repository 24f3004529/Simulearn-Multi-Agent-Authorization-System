"""Container-local copy of ../../synthetic_data.py's policy rules store."""

from __future__ import annotations

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
