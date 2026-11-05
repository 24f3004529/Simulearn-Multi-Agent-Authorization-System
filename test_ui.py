"""
Streamlit UI for testing each agent as it's implemented, without waiting for
all four to be done. Run with: streamlit run test_ui.py
"""

from __future__ import annotations

import copy
import dataclasses

import streamlit as st
from agents.clinical_extraction_agent import (CLINICAL_EXTRACTION_CARD,
                                              clinical_extraction_agent)
from agents.decision_synthesis_agent import (DECISION_SYNTHESIS_CARD,
                                             decision_synthesis_agent)
from agents.medical_necessity_agent import (MEDICAL_NECESSITY_CARD,
                                            medical_necessity_agent)
from agents.policy_matching_agent import (POLICY_MATCHING_CARD,
                                          policy_matching_agent)
from context import (FORBIDDEN_FOR_DECISION_SYNTHESIS,
                     FORBIDDEN_FOR_MEDICAL_NECESSITY,
                     FORBIDDEN_FOR_POLICY_MATCHING, IsolationViolationError,
                     assert_isolation, scope_for_decision_synthesis,
                     scope_for_medical_necessity, scope_for_policy_matching)
from failure_injector import FailureInjector
from orchestrator import AuditTrail, CheckpointStore, Orchestrator
from recovery import DeadLetterQueue, ManualReviewQueue
from registry import AgentCardRegistry
from synthetic_data import ATTACHED_DOCUMENTS, SAMPLE_REQUESTS

st.set_page_config(page_title="Prior Auth — Agent Test UI", layout="wide")


def asdict_safe(obj) -> dict:
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return {"value": repr(obj)}


def build_registry() -> tuple[AgentCardRegistry, FailureInjector]:
    registry = AgentCardRegistry()
    registry.register(CLINICAL_EXTRACTION_CARD, clinical_extraction_agent)
    registry.register(POLICY_MATCHING_CARD, policy_matching_agent)
    registry.register(MEDICAL_NECESSITY_CARD, medical_necessity_agent)
    registry.register(DECISION_SYNTHESIS_CARD, decision_synthesis_agent)
    return registry, FailureInjector()


def probe_implemented(fn, *args, **kwargs) -> tuple[bool, object | Exception]:
    # NotImplementedError means the function is a stub; any other exception
    # means it's implemented but currently raising a real bug.
    try:
        return True, fn(*args, **kwargs)
    except NotImplementedError as e:
        return False, e
    except Exception as e:  # noqa: BLE001 - intentionally broad, surfaced in the UI
        return True, e


st.title("Prior Authorization — Agent Test UI")
st.caption(
    "Test each agent as you implement it, or run the full pipeline once all four are done."
)

registry, failures = build_registry()

request_options = {r.request_id: r for r in SAMPLE_REQUESTS}
selected_id = st.selectbox(
    "Sample request",
    options=list(request_options.keys()),
    format_func=lambda rid: f"{rid} — {request_options[rid].patient_name}",
)
request = request_options[selected_id]

with st.expander("Full PA request (only Clinical Extraction sees all of this)"):
    st.json(asdict_safe(request))

st.divider()

st.subheader("1. Agent Card Discovery")
capabilities = [
    "clinical_extraction",
    "policy_matching",
    "medical_necessity",
    "decision_synthesis",
]
cols = st.columns(len(capabilities))
for col, capability in zip(cols, capabilities):
    with col:
        try:
            agent = registry.discover_by_capability(capability)
            st.success(f"**{capability}**\n\nresolved: `{agent.card.agent_name}`")
        except Exception as e:  # noqa: BLE001
            st.error(f"**{capability}**\n\n{e}")

st.divider()

st.subheader("2. Run agents individually")

clinical_summary = None
policy_match = None
necessity = None
final = None

st.markdown("#### Clinical Extraction")
ok, out = probe_implemented(clinical_extraction_agent, request, failures)
if not ok:
    st.warning("Not implemented yet.")
elif isinstance(out, Exception):
    st.error(f"Raised an error when called: {out!r}")
else:
    clinical_summary = out
    c1, c2 = st.columns(2)
    with c1:
        st.caption("Input received (full PARequest — no scoping for this agent)")
        st.json(asdict_safe(request))
    with c2:
        st.caption("Output produced (ClinicalSummary)")
        st.json(asdict_safe(clinical_summary))

st.markdown("#### Policy Matching")
scoped_pm = scope_for_policy_matching(request)
try:
    assert_isolation(
        scoped_pm, FORBIDDEN_FOR_POLICY_MATCHING, agent_name="policy-matching"
    )
    isolation_ok = True
    isolation_err = None
except IsolationViolationError as e:
    isolation_ok = False
    isolation_err = e

ok, out = probe_implemented(
    policy_matching_agent,
    procedure_codes=scoped_pm["procedure_codes"],
    plan_id=scoped_pm["plan_id"],
    line_of_business=scoped_pm["line_of_business"],
    request_id=request.request_id,
    failures=failures,
)
c1, c2 = st.columns(2)
with c1:
    st.caption("Scoped input — no clinical notes or demographics")
    st.json(scoped_pm)
    if isolation_ok:
        st.success("assert_isolation passed")
    else:
        st.error(f"assert_isolation FAILED: {isolation_err}")
with c2:
    if isinstance(out, Exception) and not isinstance(out, NotImplementedError):
        st.error(f"Raised an error when called: {out!r}")
    else:
        policy_match = out
        st.caption("Output produced (PolicyMatch, or None if no policy matches)")
        st.json(
            asdict_safe(policy_match) if policy_match is not None else {"result": None}
        )

st.markdown("#### Medical Necessity")
if clinical_summary is None or policy_match is None:
    st.info("Waiting on Clinical Extraction and/or Policy Matching output above.")
else:
    scoped_mn = scope_for_medical_necessity(
        notes_summary=clinical_summary.notes_summary,
        coverage_criteria=policy_match.coverage_criteria,
        required_docs_list=policy_match.required_docs_list,
    )
    try:
        assert_isolation(
            scoped_mn, FORBIDDEN_FOR_MEDICAL_NECESSITY, agent_name="medical-necessity"
        )
        isolation_ok = True
        isolation_err = None
    except IsolationViolationError as e:
        isolation_ok = False
        isolation_err = e

    ok, out = probe_implemented(
        medical_necessity_agent,
        notes_summary=scoped_mn["notes_summary"],
        coverage_criteria=scoped_mn["coverage_criteria"],
        required_docs_list=scoped_mn["required_docs_list"],
        request_id=request.request_id,
        failures=failures,
    )
    c1, c2 = st.columns(2)
    with c1:
        st.caption("Scoped input — no plan_id, billing_info, or subscriber_id")
        st.json(scoped_mn)
        st.caption(
            f"Attached documents for {request.request_id}: {sorted(ATTACHED_DOCUMENTS.get(request.request_id, set()))}"
        )
        if isolation_ok:
            st.success("assert_isolation passed")
        else:
            st.error(f"assert_isolation FAILED: {isolation_err}")
    with c2:
        if not ok:
            st.warning("Not implemented yet.")
        elif isinstance(out, Exception):
            st.error(f"Raised an error when called: {out!r}")
        else:
            necessity = out
            st.caption("Output produced (NecessityDetermination)")
            st.json(asdict_safe(necessity))

st.markdown("#### Decision Synthesis")
if necessity is None or policy_match is None:
    st.info("Waiting on Medical Necessity output above.")
else:
    scoped_ds = scope_for_decision_synthesis(
        necessity_result=necessity.result.value,
        evidence_citations=necessity.evidence_citations,
        missing_evidence=necessity.missing_evidence,
        policy_id=policy_match.matched_policy_id,
    )
    try:
        assert_isolation(
            scoped_ds, FORBIDDEN_FOR_DECISION_SYNTHESIS, agent_name="decision-synthesis"
        )
        isolation_ok = True
        isolation_err = None
    except IsolationViolationError as e:
        isolation_ok = False
        isolation_err = e

    ok, out = probe_implemented(
        decision_synthesis_agent,
        necessity_result=scoped_ds["necessity_result"],
        evidence_citations=scoped_ds["evidence_citations"],
        missing_evidence=scoped_ds["missing_evidence"],
        policy_id=scoped_ds["policy_id"],
        request_id=request.request_id,
        failures=failures,
    )
    c1, c2 = st.columns(2)
    with c1:
        st.caption("Scoped input — compressed summaries only, no raw upstream data")
        st.json(scoped_ds)
        if isolation_ok:
            st.success("assert_isolation passed")
        else:
            st.error(f"assert_isolation FAILED: {isolation_err}")
    with c2:
        if not ok:
            st.warning("Not implemented yet.")
        elif isinstance(out, Exception):
            st.error(f"Raised an error when called: {out!r}")
        else:
            final = out
            st.caption("Output produced (FinalDetermination)")
            st.json(asdict_safe(final))

st.divider()

st.subheader("3. Full pipeline run")
st.caption(
    "Requires all four agents implemented — exercises the same Orchestrator, recovery, and isolation logic main.py does."
)

if st.button("Run full pipeline for this request"):
    registry2, failures2 = build_registry()
    orchestrator = Orchestrator(
        registry=registry2,
        failures=failures2,
        checkpoints=CheckpointStore(),
        audit=AuditTrail(),
        manual_review=ManualReviewQueue(),
        dead_letter=DeadLetterQueue(),
    )
    try:
        result = orchestrator.run_pipeline(copy.deepcopy(request))
    except NotImplementedError as e:
        st.warning(f"At least one agent isn't implemented yet: {e}")
    except IsolationViolationError as e:
        st.error(f"Context isolation violated: {e}")
    except Exception as e:  # noqa: BLE001
        st.error(f"Unhandled error: {e!r}")
    else:
        st.success(f"final_state = {result.final_state.value}")
        st.write(
            {
                "final_state": result.final_state.value,
                "degraded_to_manual_review": result.degraded_to_manual_review,
                "dead_lettered": result.dead_lettered,
                "rollback_count": result.rollback_count,
            }
        )
        if result.final:
            st.json(asdict_safe(result.final))
        st.caption(f"Audit trail entries recorded: {len(orchestrator.audit.entries)}")
        with st.expander("Audit trail (full, unfiltered)"):
            st.json(orchestrator.audit.entries)
