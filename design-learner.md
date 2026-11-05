# Prior Authorization Multi-Agent System — Design

## 1. Infrastructure

The CloudFormation stack creates a VPC, a VS Code-on-EC2 dev environment, 5
DynamoDB tables (`AgentCardRegistryTable`, `CheckpointTable`,
`AuditTrailTable`, `DeadLetterTable`, `ManualReviewTable`), a lab-owned
S3 bucket (`AgentRuntimeCodeBucket`) for Runtime deployment zips, and a shared
execution role (`AgentRuntimeExecutionRole`) the deployed Runtimes assume.

`AgentCardRegistryTable` and `CheckpointTable` are the two tables the DIY
challenge wires for real, replacing `AgentCardRegistry`'s and
`CheckpointStore`'s in-memory dicts. `AuditTrailTable`, `DeadLetterTable`,
and `ManualReviewTable` stay as the in-memory stand-ins the local-dev path
already uses — wiring them is out of scope.

## 2. Given, working code

| File | What it is |
|---|---|
| `pa_types.py` | Shared dataclasses/enums for every agent's input/output. |
| `context.py` | `scope_for_*` functions build the narrowed payload each agent receives; `assert_isolation` raises `IsolationViolationError` if a
forbidden field leaks. |
| `registry.py` | `AgentCardRegistry` — capability-based discovery. |
| `recovery.py` | `CircuitBreaker`, `DeadLetterQueue`, `ManualReviewQueue`, `call_with_recovery`, `TransientAgentError`. One circuit breaker per
agent. |
| `failure_injector.py` | Deterministic, on-demand failure injection for the local-dev path. |
| `synthetic_data.py` | `POLICY_RULES_STORE`, `match_policy()`, `SAMPLE_REQUESTS`, `ATTACHED_DOCUMENTS`. |
| `orchestrator.py` | The state machine — wires discovery, context scoping, and recovery around every agent call. |
| `main.py` | End-to-end demo (`python3 main.py`, or `--real` against deployed Runtimes). |
| `test_ui.py` | Streamlit companion (`streamlit run test_ui.py`) for testing agents incrementally. |
| `runtime_client.py` | Bridges the registry to real deployed Runtimes — `build_real_agent_registry`, `verify_single_agent`. |
| `agents/policy_matching_agent.py` | A complete, working agent implementation and reference pattern. |

## 3. Per-agent work

| Agent | Concept | Local logic | Runtime deployment |
|---|---|---|---|
| Policy Matching | Lookup with absence handling | Given, complete | Deploy as-is — `agents/policy_matching_runtime/` is already correct. |
| Medical Necessity | Ternary rule evaluation + Nova 2 Lite call | Given, complete | `agents/medical_necessity_runtime/` is a fully-filled reference — deploy it as shown. |
| Clinical Extraction | Input normalization | Implement in `agents/clinical_extraction_agent.py` | `agents/clinical_extraction_runtime/` is a scaffold — fill in
`server.py`, then deploy and verify. |
| Decision Synthesis | Aggregation and explanation | Implement in `agents/decision_synthesis_agent.py` | `agents/decision_synthesis_runtime/` is a scaffold — fill in `server.py`,
then deploy and verify. |

Each agent's own docstring has the exact input/output contract and hints. The
Concept column is what makes each agent's practice distinct rather than four
repeats of the same "look something up and return it" shape — see
`requirements.md` FR-5 for the full explanation of each concept.


## 4. Architecture at a glance

PARequest
   │  (full request — nothing scoped yet, first in pipeline)
   ▼
Clinical Extraction
   │ ClinicalSummary
   ▼
Policy Matching
   │ PolicyMatch (or None → PEND)
   ▼
Medical Necessity
   │ NecessityDetermination
   │   ├─ MET / NOT_MET → continue to Decision Synthesis
   │   └─ INSUFFICIENT_EVIDENCE → PEND (no doc gap is ever a denial)
   ▼
Decision Synthesis
   │ FinalDetermination (approve / deny / pend)
   ▼
COMPLETE

Every arrow above is mediated by the `Orchestrator`: discovery via
`AgentCardRegistry.discover_by_capability(...)`, then context-scoping +
`assert_isolation`, then `call_with_recovery` (retry + per-agent circuit
breaker). Locally, `agent.invoke_fn` is an in-process function; against real
infrastructure (`main.py --real`), it is a real `InvokeAgentRuntime` call —
the Orchestrator's own code is identical either way.

The `PEND -> EXTRACTION` rollback (new documentation arrives for a pended
request) re-runs from `EXTRACTION`, not from `INTAKE` — see
`Orchestrator.rollback_and_resume`.

## 5. Verification checklist

### Local (in-process)
1. `python3 main.py` runs to completion with no unhandled exception.
2. The 3 sample requests each reach a sensible `final_state`; `PA-0002`'s
   rollback demo shows `rollback_count = 1`.
3. All 4 capabilities resolve via `discover_by_capability`.
4. The forced single-failure demo recovers via retry; the forced
   repeated-failure demo shows the circuit breaker opening and the request
   landing in `manual_review` or `dead_letter`.
5. No `IsolationViolationError` is raised.
6. `medical_necessity_agent.py` returns `INSUFFICIENT_EVIDENCE` (not
   `NOT_MET`) whenever any required document is missing — check against
   `PA-0002`/`PA-0003`, both deliberately missing `imaging_report`.
7. `streamlit run test_ui.py` exercises each agent individually as it's
   implemented.

### Real Runtime deployment (per agent)
8. The deployment zip has its entrypoint at the root — zip from inside the
   folder, not the folder itself.
9. `create-agent-runtime` includes `--protocol-configuration
   '{"serverProtocol": "A2A"}'` — it does not default to A2A.
10. The Runtime reaches `status: "READY"`.
11. `runtime_client.verify_single_agent(<capability>, <arn>)` succeeds for
   that one agent, independent of the other 3.

### Full deployment (once all 4 pass step 11)
12. All 4 ARNs are recorded in `runtime_config.py`.
13. `python3 main.py --real` reproduces the same discovery, orchestration,
   recovery, and isolation behavior as the local run, now over the network.


## 6. Out of scope

Real Nova/foundation model calls in Clinical Extraction, Policy Matching, or
Decision Synthesis (all three stay deterministic and rule-based; Medical
Necessity's met/not_met judgment is the one exception — see
`requirements.md` NFR-1), model output quality evaluation, a UI/API layer
beyond `test_ui.py`, multi-tenant or concurrent-request handling, and wiring
`AuditTrailTable`, `DeadLetterTable`, or `ManualReviewTable` (those three
stay in-memory). Peer-to-peer agent calls that bypass the Orchestrator are
out of scope except the one Medical Necessity -> Policy Matching call built
as the DIY challenge (see `peer_clarification_diy_requirements.md`). No new
shared circuit breaker — the per-agent breaker design in `recovery.py` is
intentional.
