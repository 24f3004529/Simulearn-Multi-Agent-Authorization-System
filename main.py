"""
End-to-end demo of the prior authorization pipeline. Run with:
    python3 main.py           # in-process agents (local dev)
    python3 main.py --real    # real deployed AgentCore Runtimes
"""

from __future__ import annotations

from agents.clinical_extraction_agent import (CLINICAL_EXTRACTION_CARD,
                                              clinical_extraction_agent)
from agents.decision_synthesis_agent import (DECISION_SYNTHESIS_CARD,
                                             decision_synthesis_agent)
from agents.medical_necessity_agent import (MEDICAL_NECESSITY_CARD,
                                            medical_necessity_agent)
from agents.policy_matching_agent import (POLICY_MATCHING_CARD,
                                          policy_matching_agent)
from failure_injector import FailureInjector
from orchestrator import AuditTrail, CheckpointStore, Orchestrator
from recovery import DeadLetterQueue, ManualReviewQueue
from registry import AgentCardRegistry
from synthetic_data import ATTACHED_DOCUMENTS, SAMPLE_REQUESTS


def build_orchestrator() -> tuple[Orchestrator, AgentCardRegistry, FailureInjector]:
    registry = AgentCardRegistry()
    registry.register(CLINICAL_EXTRACTION_CARD, clinical_extraction_agent)
    registry.register(POLICY_MATCHING_CARD, policy_matching_agent)
    registry.register(MEDICAL_NECESSITY_CARD, medical_necessity_agent)
    registry.register(DECISION_SYNTHESIS_CARD, decision_synthesis_agent)

    failures = FailureInjector()
    checkpoints = CheckpointStore()
    audit = AuditTrail()
    manual_review = ManualReviewQueue()
    dead_letter = DeadLetterQueue()

    orchestrator = Orchestrator(
        registry=registry,
        failures=failures,
        checkpoints=checkpoints,
        audit=audit,
        manual_review=manual_review,
        dead_letter=dead_letter,
    )
    return orchestrator, registry, failures


def build_real_orchestrator() -> (
    tuple[Orchestrator, AgentCardRegistry, FailureInjector]
):
    # Every capability is backed by a deployed Runtime instead of a local
    # function. Requires runtime_config.AGENT_RUNTIME_ARNS to be filled in.
    # Forcing failures has no effect against a real Runtime.
    from runtime_client import (build_real_agent_registry,
                                require_all_registered)
    from runtime_config import AGENT_RUNTIME_ARNS

    registry = build_real_agent_registry(AGENT_RUNTIME_ARNS)
    require_all_registered(
        registry,
        [
            "clinical_extraction",
            "policy_matching",
            "medical_necessity",
            "decision_synthesis",
        ],
    )

    failures = FailureInjector()
    checkpoints = CheckpointStore()
    audit = AuditTrail()
    manual_review = ManualReviewQueue()
    dead_letter = DeadLetterQueue()

    orchestrator = Orchestrator(
        registry=registry,
        failures=failures,
        checkpoints=checkpoints,
        audit=audit,
        manual_review=manual_review,
        dead_letter=dead_letter,
    )
    return orchestrator, registry, failures


def print_header(text: str) -> None:
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}")


def demonstrate_discovery(registry: AgentCardRegistry) -> None:
    print_header("Agent card discovery")
    for capability in [
        "clinical_extraction",
        "policy_matching",
        "medical_necessity",
        "decision_synthesis",
    ]:
        agent = registry.discover_by_capability(capability)
        print(
            f"  discover_by_capability('{capability}') -> resolved agent: {agent.card.agent_name}"
        )


def demonstrate_happy_and_pend_paths(orchestrator: Orchestrator) -> None:
    print_header("Orchestration — happy path, partial evidence, and no-match PEND")
    for request in SAMPLE_REQUESTS:
        result = orchestrator.run_pipeline(request)
        print(f"\n  Request {request.request_id} ({request.patient_name}):")
        print(f"    final_state = {result.final_state.value}")
        if result.final:
            print(f"    decision    = {result.final.decision.value}")
            print(f"    rationale   = {result.final.rationale}")


def demonstrate_rollback(orchestrator: Orchestrator) -> None:
    print_header("PEND -> EXTRACTION rollback on resubmission")
    request = SAMPLE_REQUESTS[1]
    print(f"  Simulating new documentation arriving for {request.request_id}...")
    ATTACHED_DOCUMENTS[request.request_id].add("imaging_report")
    result = orchestrator.rollback_and_resume(request)
    print(f"    rollback_count = {result.rollback_count}")
    print(f"    final_state    = {result.final_state.value}")
    if result.final:
        print(f"    decision       = {result.final.decision.value}")


def demonstrate_recovery(orchestrator: Orchestrator, failures: FailureInjector) -> None:
    print_header("Recovery — forced transient failure and retry")
    request = SAMPLE_REQUESTS[0]
    failures.force_failures("policy-matching", count=1)
    result = orchestrator.run_pipeline(request)
    print(
        f"  Request {request.request_id} with 1 forced transient failure on policy-matching:"
    )
    print(
        f"    final_state = {result.final_state.value}  (recovered via retry, not dead-lettered)"
    )

    print_header("Recovery — circuit breaker opens after repeated failures")
    failures.force_failures("medical-necessity", count=10)
    result = orchestrator.run_pipeline(SAMPLE_REQUESTS[2])
    print(
        f"  Request {SAMPLE_REQUESTS[2].request_id} with medical-necessity forced to fail repeatedly:"
    )
    print(f"    final_state               = {result.final_state.value}")
    print(f"    degraded_to_manual_review = {result.degraded_to_manual_review}")
    print(f"    dead_lettered             = {result.dead_lettered}")
    print(f"    manual_review queue size  = {len(orchestrator.manual_review)}")
    print(f"    dead_letter queue size    = {len(orchestrator.dead_letter)}")


def demonstrate_context_isolation(orchestrator: Orchestrator) -> None:
    print_header("Context isolation held (no exception raised)")
    print("  No IsolationViolationError was raised, so:")
    print("    - Policy Matching never received clinical_notes/patient demographics")
    print("    - Medical Necessity never received plan_id/billing_info/subscriber_id")
    print("    - Decision Synthesis never received raw clinical_notes/billing_info")
    print(
        f"  Audit trail captured {len(orchestrator.audit.entries)} full, unfiltered entries."
    )


def main() -> None:
    import sys

    use_real_runtimes = "--real" in sys.argv[1:]
    if use_real_runtimes:
        print_header("Running against real deployed AgentCore Runtimes (--real)")
        orchestrator, registry, failures = build_real_orchestrator()
    else:
        orchestrator, registry, failures = build_orchestrator()
    demonstrate_discovery(registry)
    demonstrate_happy_and_pend_paths(orchestrator)
    demonstrate_rollback(orchestrator)
    if not use_real_runtimes:
        demonstrate_recovery(orchestrator, failures)
    demonstrate_context_isolation(orchestrator)
    print("\n" + "=" * 70)
    print("Run complete.")
    print("=" * 70)


if __name__ == "__main__":
    main()
