"""
Combined verification for the DIY challenges: Part 1 (DynamoDB-backed
AgentCardRegistry and CheckpointStore) and Part 2 (Medical Necessity's peer
clarification call to Policy Matching).

Each check only calls the public interface (register/get_card/is_available,
save/load, verify_single_agent) — never assumes a specific DynamoDB item
shape, since that's an implementation detail of your own DIY code.

The real proof for Part 1 is that data survives a fresh process, not just a
fresh object in the same process — an in-memory dict would pass a same
process test just as well as a real DynamoDB-backed one. This script re-runs
itself as a subprocess for the "load" side of each check to prove that.

Usage: python3 test_diy.py
"""

from __future__ import annotations

import subprocess
import sys

TEST_AGENT_NAME = "test-diy-verify-agent"
TEST_REQUEST_ID = "TEST-DIY-VERIFY-001"

# Mirrors PUAssignmentValidation's validate_assignment_069528ea.EXPECTED_CAPABILITIES.
# The real validator fails REGISTRY_MISSING_CAPABILITIES if any of these four are
# absent from AgentCardRegistryTable, so a local check that registers and verifies
# only one synthetic capability can print OK here while still failing the graded
# validation. Keep this set in sync by hand if the validator's set ever changes.
EXPECTED_CAPABILITIES = ("clinical_extraction", "policy_matching", "medical_necessity", "decision_synthesis")


def _print_result(label: str, ok: bool, detail: str = "") -> None:
    status = "OK" if ok else "FAILED"
    suffix = f" -> {detail}" if detail else ""
    print(f"{label}: {status}{suffix}")


def _save_registry_entry() -> None:
    from pa_types import AgentCard
    from registry import AgentCardRegistry

    registry = AgentCardRegistry()
    # Register one card per EXPECTED_CAPABILITIES, same shape as main.py's
    # build_orchestrator() registering the four real agents — not one synthetic
    # card/capability — so a fresh process can discover every capability the real
    # validator requires, not just one.
    for capability in EXPECTED_CAPABILITIES:
        registry.register(
            AgentCard(
                agent_name=f"{TEST_AGENT_NAME}-{capability}",
                capabilities=[capability],
                input_schema={},
                output_schema={},
                version="1.0",
            ),
            invoke_fn=lambda *a, **k: None,
        )


def _load_registry_entry() -> None:
    from registry import AgentCardRegistry

    registry = AgentCardRegistry()
    missing: list[str] = []
    for capability in EXPECTED_CAPABILITIES:
        try:
            # discover_by_capability (not is_available/get_card, which key on
            # agent_name) is the lookup path the validator's REGISTRY_MISSING_
            # CAPABILITIES check is proving: can a capability be resolved from
            # AgentCardRegistryTable in a fresh process.
            registry.discover_by_capability(capability)
        except Exception:
            missing.append(capability)

    if len(missing) == len(EXPECTED_CAPABILITIES):
        raise AssertionError(
            "discover_by_capability() failed for every expected capability in a "
            "fresh process — register() likely never wrote to AgentCardRegistryTable."
        )
    if missing:
        raise AssertionError(
            f"discover_by_capability() failed for: {missing}. The real validator "
            f"fails REGISTRY_MISSING_CAPABILITIES for this — check that register() "
            f"writes ALL capabilities, not just some, to AgentCardRegistryTable."
        )


def check_registry_part1() -> bool:
    try:
        _save_registry_entry()
        subprocess.run(
            [sys.executable, __file__, "--_load-registry"], check=True, capture_output=True, text=True
        )
        _print_result("DIY Part 1 — registry (cross-process)", True)
        return True
    except subprocess.CalledProcessError as exc:
        _print_result("DIY Part 1 — registry (cross-process)", False, exc.stderr.strip().splitlines()[-1])
        return False
    except Exception as exc:  # noqa: BLE001 - surface the real failure, don't mask it
        _print_result("DIY Part 1 — registry (cross-process)", False, str(exc))
        return False


def _save_checkpoint_entry() -> None:
    from orchestrator import Checkpoint, CheckpointStore
    from pa_types import NecessityDetermination, NecessityResult, PAState

    store = CheckpointStore()
    store.save(
        Checkpoint(
            request_id=TEST_REQUEST_ID,
            state=PAState.MED_NECESSITY,
            necessity=NecessityDetermination(
                request_id=TEST_REQUEST_ID,
                result=NecessityResult.MET,
                evidence_citations=["Verification citation."],
            ),
        )
    )


def _load_checkpoint_entry() -> None:
    from orchestrator import CheckpointStore
    from pa_types import NecessityResult, PAState

    store = CheckpointStore()
    checkpoint = store.load(TEST_REQUEST_ID)
    if checkpoint is None:
        raise AssertionError(
            "load() returned None in a fresh process — save() likely never wrote to CheckpointTable."
        )
    if checkpoint.state != PAState.MED_NECESSITY:
        raise AssertionError(f"Checkpoint.state did not round-trip correctly: {checkpoint.state!r}")
    if checkpoint.necessity is None or not isinstance(checkpoint.necessity.result, NecessityResult):
        raise AssertionError(
            f"Checkpoint.necessity.result did not reconstruct as a real NecessityResult enum: "
            f"{checkpoint.necessity!r}"
        )


def check_checkpoint_part1() -> bool:
    try:
        _save_checkpoint_entry()
        subprocess.run(
            [sys.executable, __file__, "--_load-checkpoint"], check=True, capture_output=True, text=True
        )
        _print_result("DIY Part 1 — checkpoint (cross-process, enum-aware)", True)
        return True
    except subprocess.CalledProcessError as exc:
        _print_result("DIY Part 1 — checkpoint (cross-process, enum-aware)", False, exc.stderr.strip().splitlines()[-1])
        return False
    except Exception as exc:  # noqa: BLE001 - surface the real failure, don't mask it
        _print_result("DIY Part 1 — checkpoint (cross-process, enum-aware)", False, str(exc))
        return False


def check_peer_clarification_part2() -> bool:
    from runtime_client import verify_single_agent
    from runtime_config import AGENT_RUNTIME_ARNS

    runtime_arn = AGENT_RUNTIME_ARNS.get("medical_necessity")
    if not runtime_arn:
        _print_result("DIY Part 2 — peer clarification", False, "no medical_necessity ARN in runtime_config.py")
        return False

    try:
        # verify_single_agent()'s medical_necessity sample requests
        # imaging_report but never supplies it, so this always exercises the
        # insufficient_evidence -> peer clarification path.
        result = verify_single_agent("medical_necessity", runtime_arn)
        peer_clarification = getattr(result, "peer_clarification", None) or {}
        if not peer_clarification.get("requested"):
            _print_result(
                "DIY Part 2 — peer clarification", False,
                f"Runtime did not request peer clarification for missing evidence: {result!r}",
            )
            return False
        if not peer_clarification.get("missing_documents"):
            _print_result(
                "DIY Part 2 — peer clarification", False,
                f"peer_clarification.requested was True but missing_documents was empty: {peer_clarification!r}",
            )
            return False
        _print_result("DIY Part 2 — peer clarification", True, f"missing_documents={peer_clarification['missing_documents']}")
        return True
    except Exception as exc:  # noqa: BLE001 - surface the real failure, don't mask it
        _print_result("DIY Part 2 — peer clarification", False, str(exc))
        return False


def main() -> None:
    if "--_load-registry" in sys.argv:
        _load_registry_entry()
        return
    if "--_load-checkpoint" in sys.argv:
        _load_checkpoint_entry()
        return

    print("Running DIY verification checks...\n")
    results = [
        check_registry_part1(),
        check_checkpoint_part1(),
        check_peer_clarification_part2(),
    ]
    print(f"\n{sum(results)}/{len(results)} checks passed.")


if __name__ == "__main__":
    main()
