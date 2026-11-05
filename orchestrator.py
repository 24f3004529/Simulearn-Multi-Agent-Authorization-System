"""
Drives a prior authorization request through the pipeline (extraction, policy
match, medical necessity, decision synthesis) with checkpointing, recovery,
and PEND -> EXTRACTION rollback when new documentation arrives.

States: INTAKE -> EXTRACTION -> POLICY_MATCH -> MED_NECESSITY ->
DETERMINATION -> COMPLETE, with MED_NECESSITY -> PEND on insufficient
evidence, no policy match -> PEND, and any exhausted agent -> DEAD_LETTER or
manual review.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from context import (FORBIDDEN_FOR_DECISION_SYNTHESIS,
                     FORBIDDEN_FOR_MEDICAL_NECESSITY,
                     FORBIDDEN_FOR_POLICY_MATCHING, assert_isolation,
                     scope_for_decision_synthesis, scope_for_medical_necessity,
                     scope_for_policy_matching)
from failure_injector import FailureInjector
from pa_types import (ClinicalSummary, FinalDetermination,
                      NecessityDetermination, NecessityResult, PARequest,
                      PAState, PolicyMatch)
from recovery import (CircuitBreaker, DeadLetterQueue, ManualReviewQueue,
                      RecoveryExhaustedError, call_with_recovery)
from registry import AgentCardRegistry


@dataclass
class Checkpoint:
    """One saved state per request, per phase."""

    request_id: str
    state: PAState
    clinical_summary: ClinicalSummary | None = None
    policy_match: PolicyMatch | None = None
    necessity: NecessityDetermination | None = None
    final: FinalDetermination | None = None


class CheckpointStore:
    def __init__(self) -> None:
        self._store: dict[str, Checkpoint] = {}

    def save(self, checkpoint: Checkpoint) -> None:
        # TODO(DIY Part 1): also write `checkpoint` to CheckpointTable here.
        self._store[checkpoint.request_id] = checkpoint

    def load(self, request_id: str) -> Checkpoint | None:
        # TODO(DIY Part 1): read from CheckpointTable instead of self._store.
        return self._store.get(request_id)


@dataclass
class AuditTrail:
    """The one place that holds every agent's full, unfiltered payload for
    compliance review — every agent itself only ever sees a scoped subset."""

    entries: list[dict] = field(default_factory=list)

    def record(self, request_id: str, phase: str, payload: dict) -> None:
        self.entries.append(
            {"request_id": request_id, "phase": phase, "payload": payload}
        )


class PARequestResult:
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.final_state: PAState = PAState.INTAKE
        self.final: FinalDetermination | None = None
        self.degraded_to_manual_review = False
        self.dead_lettered = False
        self.rollback_count = 0


class Orchestrator:
    def __init__(
        self,
        registry: AgentCardRegistry,
        failures: FailureInjector,
        checkpoints: CheckpointStore,
        audit: AuditTrail,
        manual_review: ManualReviewQueue,
        dead_letter: DeadLetterQueue,
    ) -> None:
        self.registry = registry
        self.failures = failures
        self.checkpoints = checkpoints
        self.audit = audit
        self.manual_review = manual_review
        self.dead_letter = dead_letter
        # One circuit breaker per agent — a failure in one must not trip another's.
        self._breakers: dict[str, CircuitBreaker] = {
            "clinical-extraction": CircuitBreaker(),
            "policy-matching": CircuitBreaker(),
            "medical-necessity": CircuitBreaker(),
            "decision-synthesis": CircuitBreaker(),
        }

    def resume_from_checkpoint(self, request_id: str) -> Checkpoint | None:
        return self.checkpoints.load(request_id)

    def run_pipeline(
        self, request: PARequest, *, resubmission_docs: bool = False
    ) -> PARequestResult:
        result = PARequestResult(request.request_id)
        state = PAState.INTAKE
        checkpoint = self.checkpoints.load(request.request_id) or Checkpoint(
            request_id=request.request_id, state=state
        )

        state = PAState.EXTRACTION
        try:
            clinical_summary = self._invoke_clinical_extraction(request)
        except RecoveryExhaustedError:
            return self._degrade_or_dead_letter(
                result, request.request_id, "clinical-extraction"
            )
        checkpoint.state = state
        checkpoint.clinical_summary = clinical_summary
        self.checkpoints.save(checkpoint)
        self.audit.record(
            request.request_id, "EXTRACTION", {"clinical_summary": clinical_summary}
        )

        state = PAState.POLICY_MATCH
        try:
            policy_match = self._invoke_policy_matching(request)
        except RecoveryExhaustedError:
            return self._degrade_or_dead_letter(
                result, request.request_id, "policy-matching"
            )
        if policy_match is None:
            # No policy matches this request — pend rather than fail silently.
            result.final_state = PAState.PEND
            self.audit.record(
                request.request_id,
                "POLICY_MATCH",
                {"policy_match": None, "reason": "no_match"},
            )
            return result
        checkpoint.state = state
        checkpoint.policy_match = policy_match
        self.checkpoints.save(checkpoint)
        self.audit.record(
            request.request_id, "POLICY_MATCH", {"policy_match": policy_match}
        )

        state = PAState.MED_NECESSITY
        try:
            necessity = self._invoke_medical_necessity(clinical_summary, policy_match)
        except RecoveryExhaustedError:
            return self._degrade_or_dead_letter(
                result, request.request_id, "medical-necessity"
            )
        checkpoint.state = state
        checkpoint.necessity = necessity
        self.checkpoints.save(checkpoint)
        self.audit.record(request.request_id, "MED_NECESSITY", {"necessity": necessity})

        # TODO(DIY Part 2): if necessity.peer_clarification indicates a peer
        # call was requested, record it to self.audit here, attributed as
        # agent-initiated, before the early return below.

        if necessity.result == NecessityResult.INSUFFICIENT_EVIDENCE:
            checkpoint.state = PAState.PEND
            self.checkpoints.save(checkpoint)
            result.final_state = PAState.PEND
            self.audit.record(
                request.request_id,
                "PEND",
                {"missing_evidence": necessity.missing_evidence},
            )
            return result

        state = PAState.DETERMINATION
        try:
            final = self._invoke_decision_synthesis(necessity, policy_match)
        except RecoveryExhaustedError:
            return self._degrade_or_dead_letter(
                result, request.request_id, "decision-synthesis"
            )
        checkpoint.state = PAState.COMPLETE
        checkpoint.final = final
        self.checkpoints.save(checkpoint)
        self.audit.record(request.request_id, "COMPLETE", {"final": final})

        result.final_state = PAState.COMPLETE
        result.final = final
        return result

    def rollback_and_resume(self, request: PARequest) -> PARequestResult:
        """New documentation arrived for a pended request — re-run from
        EXTRACTION rather than restarting from INTAKE."""
        result = self.run_pipeline(request, resubmission_docs=True)
        result.rollback_count = 1
        return result

    # Per-agent invocation: discover by capability, scope the input, assert
    # isolation, then call through the circuit breaker.

    def _invoke_clinical_extraction(self, request: PARequest) -> ClinicalSummary:
        agent = self.registry.discover_by_capability("clinical_extraction")
        breaker = self._breakers["clinical-extraction"]
        return call_with_recovery(
            lambda: agent.invoke_fn(request, self.failures),
            agent_name="clinical-extraction",
            breaker=breaker,
        )

    def _invoke_policy_matching(self, request: PARequest) -> PolicyMatch | None:
        agent = self.registry.discover_by_capability("policy_matching")
        breaker = self._breakers["policy-matching"]
        scoped = scope_for_policy_matching(request)
        assert_isolation(
            scoped, FORBIDDEN_FOR_POLICY_MATCHING, agent_name="policy-matching"
        )
        return call_with_recovery(
            lambda: agent.invoke_fn(
                procedure_codes=scoped["procedure_codes"],
                plan_id=scoped["plan_id"],
                line_of_business=scoped["line_of_business"],
                request_id=request.request_id,
                failures=self.failures,
            ),
            agent_name="policy-matching",
            breaker=breaker,
        )

    def _invoke_medical_necessity(
        self, clinical_summary: ClinicalSummary, policy_match: PolicyMatch
    ) -> NecessityDetermination:
        agent = self.registry.discover_by_capability("medical_necessity")
        breaker = self._breakers["medical-necessity"]
        scoped = scope_for_medical_necessity(
            notes_summary=clinical_summary.notes_summary,
            coverage_criteria=policy_match.coverage_criteria,
            required_docs_list=policy_match.required_docs_list,
        )
        assert_isolation(
            scoped, FORBIDDEN_FOR_MEDICAL_NECESSITY, agent_name="medical-necessity"
        )
        return call_with_recovery(
            lambda: agent.invoke_fn(
                notes_summary=scoped["notes_summary"],
                coverage_criteria=scoped["coverage_criteria"],
                required_docs_list=scoped["required_docs_list"],
                request_id=clinical_summary.request_id,
                failures=self.failures,
            ),
            agent_name="medical-necessity",
            breaker=breaker,
        )

    def _invoke_decision_synthesis(
        self, necessity: NecessityDetermination, policy_match: PolicyMatch
    ) -> FinalDetermination:
        agent = self.registry.discover_by_capability("decision_synthesis")
        breaker = self._breakers["decision-synthesis"]
        scoped = scope_for_decision_synthesis(
            necessity_result=necessity.result.value,
            evidence_citations=necessity.evidence_citations,
            missing_evidence=necessity.missing_evidence,
            policy_id=policy_match.matched_policy_id,
        )
        assert_isolation(
            scoped, FORBIDDEN_FOR_DECISION_SYNTHESIS, agent_name="decision-synthesis"
        )
        return call_with_recovery(
            lambda: agent.invoke_fn(
                necessity_result=scoped["necessity_result"],
                evidence_citations=scoped["evidence_citations"],
                missing_evidence=scoped["missing_evidence"],
                policy_id=scoped["policy_id"],
                request_id=necessity.request_id,
                failures=self.failures,
            ),
            agent_name="decision-synthesis",
            breaker=breaker,
        )

    def _degrade_or_dead_letter(
        self, result: PARequestResult, request_id: str, agent_name: str
    ) -> PARequestResult:
        """Route to manual review if the circuit is open; dead-letter as the
        last resort so nothing is silently dropped."""
        breaker = self._breakers[agent_name]
        if breaker.state.value == "open":
            self.manual_review.add(
                request_id, agent_name, reason="circuit_breaker_open"
            )
            result.degraded_to_manual_review = True
        else:
            self.dead_letter.add(request_id, agent_name, reason="retries_exhausted")
            result.dead_lettered = True
        result.final_state = (
            PAState.DEAD_LETTER if result.dead_lettered else PAState.PEND
        )
        return result
