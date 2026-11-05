"""Container-local copy of ../../synthetic_data.py's ATTACHED_DOCUMENTS."""

from __future__ import annotations

ATTACHED_DOCUMENTS: dict[str, set[str]] = {
    "PA-0001": set(),
    "PA-0002": {"chart_notes_last_90_days"},
    "PA-0003": {"chart_notes_last_90_days", "conservative_treatment_log"},
}
