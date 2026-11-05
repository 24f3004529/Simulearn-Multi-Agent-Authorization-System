# Prior Authorization Multi-Agent System — Requirements

## Overview

A 4-agent Prior Authorization (PA) workflow: a request moves through Clinical
Extraction, Policy Matching, Medical Necessity, and Decision Synthesis agents,
coordinated by an Orchestrator that implements a state machine with
checkpointing, rollback, and recovery. Each agent runs as its own real
Bedrock AgentCore Runtime, discovered and invoked over the network via A2A
(`GetAgentCard`/`InvokeAgentRuntime`), not as an in-process function call.

## Pre-Provisioned Infrastructure

The CloudFormation stack creates a VPC, a VS Code-on-EC2 dev environment, 5
DynamoDB tables (agent card registry, checkpoint, audit trail, dead-letter,
manual-review), a lab-owned S3 bucket for Runtime deployment zips
(`AgentRuntimeCodeBucket`), and a shared execution role for the deployed
Runtimes (`AgentRuntimeExecutionRole`).

Of the 5 tables, the Orchestrator wires two for real, as the DIY challenge:
`AgentCardRegistryTable` (backing `AgentCardRegistry`) and `CheckpointTable`
(backing `CheckpointStore`) — see FR-1 and FR-2. The remaining three
(`AuditTrailTable`, `DeadLetterTable`, `ManualReviewTable`) stay as the
in-memory stand-ins the local-dev path already uses; wiring them is out of
scope for this lab.

## Functional Requirements

### FR-1: Agent Card discovery (A2A)
- Every agent publishes an `AgentCard` (capabilities, input/output schema,
  version). Locally, this is registered directly with `AgentCardRegistry`;
  once deployed as a Runtime, `GetAgentCard` against the Runtime's real ARN
  is what the registry resolves instead.
- The Orchestrator never calls an agent by a hardcoded reference — every
  invocation resolves the target agent via
  `registry.discover_by_capability(<capability-string>)`.
- Discovery raises `AgentNotFoundError` if no agent advertises the requested
  capability, and `AgentVersionMismatchError` if only an incompatible version
  is registered.
- A Runtime's registered capability is never trusted from configuration
  alone — it is confirmed against that Runtime's own real `GetAgentCard`
  response before being registered.
- **DIY extension**: `AgentCardRegistry`'s in-memory dict is backed by
  `AgentCardRegistryTable` instead, so registered cards survive an
  Orchestrator restart rather than being lost with the process. See the DIY
  requirements doc.

### FR-2: Orchestration state machine
- The Orchestrator drives every request through the exact state machine:
  `INTAKE -> EXTRACTION -> POLICY_MATCH -> MED_NECESSITY -> DETERMINATION -> COMPLETE`
- `MED_NECESSITY -> PEND` when evidence is insufficient (not a denial).
- Policy lookup returning no match routes to `PEND`, not an unhandled
  exception or silent failure.
- `PEND -> EXTRACTION` (rollback): when new documentation arrives for a
  pended request, the pipeline re-runs from `EXTRACTION`, not from `INTAKE`,
  and does not require a full re-submission.
- State is checkpointed after every successful phase transition; a
  pended/in-progress request is resumable from its last checkpoint.
- **DIY extension**: `CheckpointStore`'s in-memory dict is backed by
  `CheckpointTable` instead, so a pended/in-progress request is resumable
  after an Orchestrator restart, not just within the same process. See the
  DIY requirements doc.

### FR-3: Recovery semantics
- Every agent invocation is wrapped in retry-with-backoff, gated by a
  **per-agent** circuit breaker — a failure in one agent must never block
  calls to a different, healthy agent.
- If an agent's circuit breaker is open when a request needs it, the request
  routes to the **manual review queue** (graceful degradation), not a hard
  failure.
- If retries are exhausted but the breaker has not tripped open, the request
  routes to the **dead-letter queue** instead — two distinct, both-reachable
  outcomes.
- No request is silently dropped: every failure path ends in a completed
  determination, a PEND, a manual-review entry, or a dead-letter entry.
- Recovery wraps the real network call to each agent's Runtime the same way
  it wraps an in-process call — a deployed Runtime introduces real transient
  failure modes (cold starts, throttling) that this same retry/circuit-breaker
  logic is built to handle.

### FR-4: Context engineering / data minimization
Each agent receives only:

| Agent | Receives | Must NOT receive |
|---|---|---|
| Clinical Extraction | Full PA request | (none — first in pipeline) |
| Policy Matching | Procedure codes, plan ID, line of business | Clinical notes, patient demographics |
| Medical Necessity | Clinical summary, policy coverage criteria, required docs list | Plan details, billing info, subscriber ID |
| Decision Synthesis | Compressed necessity result, evidence citations, missing evidence, policy ID | Raw clinical notes, raw policy text, billing info |
| Audit Trail | Full, unfiltered context from every phase | (none — the one deliberate exception, for compliance) |

- Every agent handoff is scoped through the matching `scope_for_*` function
  and passes an `assert_isolation()` check before the agent is invoked — a
  violation raises `IsolationViolationError` and aborts the request.
- The audit trail is the only store that ever holds full, unscoped context.
- Scoping happens before the network call is built, so a deployed Runtime
  never receives more than the same scoped payload an in-process call would
  have passed.

### FR-5: Agent logic

Each agent teaches a distinct data-handling concept, on top of the shared A2A
deployment mechanics common to all 4 (see FR-6):

- **Clinical Extraction** — *input normalization*: given the full PA request,
  produces a `ClinicalSummary` (ICD-10 codes, CPT codes, a compressed notes
  summary). This is the pipeline's entry point, so it is the only agent that
  turns an unstructured/free-form field into structured downstream input.
- **Policy Matching** — *lookup with absence handling*: given procedure
  codes, plan ID, and line of business, looks up the policy rules store and
  returns a `PolicyMatch`, or `None` if no policy matches. The concept here
  is a query that can legitimately return nothing, and routing that absence
  to `PEND` rather than treating it as an error.
- **Medical Necessity** — *ternary rule evaluation + one model call*: given
  a clinical summary and matched policy criteria, produces a
  `NecessityDetermination` (met / not_met / insufficient_evidence, with
  evidence citations and, for insufficient evidence, the specific missing
  documents). Any missing required documentation — partial or complete —
  produces `insufficient_evidence`, never an outright denial for a
  paperwork gap; this check stays deterministic. Once documentation is
  complete, the met/not_met judgment itself calls Amazon Nova 2 Lite. This
  is the one agent whose decision space isn't binary pass/fail.
  For the DIY challenge, this agent also makes a real peer-to-peer A2A call
  to Policy Matching for clarification when evidence is insufficient — see
  the DIY requirements doc.
- **Decision Synthesis** — *aggregation and explanation*: given a necessity
  determination and policy ID, produces a `FinalDetermination` (approve /
  deny / pend, with a rationale and next actions). The concept here is
  combining an upstream result with a second input (policy ID) into an
  outcome a human can act on, not just a raw decision.

### FR-6: Real AgentCore Runtime deployment
- Each of the 4 agents is deployed as its own Bedrock AgentCore Runtime via
  direct code deployment (a plain zip, no containers), implementing the A2A
  protocol contract (JSON-RPC 2.0 at `POST /`, `GET /.well-known/agent-card.json`,
  `GET /ping`).
- Deployment requires `--protocol-configuration serverProtocol=A2A`
  explicitly — it does not default to A2A.
- The entrypoint file must be at the root of the deployment zip.
- Each deployed Runtime is verified independently — `GetAgentCard` and a real
  `InvokeAgentRuntime` call against representative input — before being
  wired into the Orchestrator.
- Once all 4 Runtimes are deployed and individually verified, the full
  pipeline runs against all 4 over the network, reproducing the same
  discovery, orchestration, recovery, and isolation behavior as the
  in-process path.

## Non-Functional Requirements

### NFR-1: Determinism and reproducibility
- Clinical Extraction, Policy Matching, and Decision Synthesis are fully
  deterministic and rule-based against the synthetic data stores provided.
  Medical Necessity is the exception: once required documentation is
  complete, it calls Amazon Nova 2 Lite (Bedrock Converse API) to judge
  met/not_met — a real, non-deterministic model call. Its missing-document
  check, which decides `insufficient_evidence`, stays deterministic.
- Failure injection (`FailureInjector.force_failures`) is on-demand and
  deterministic, not randomized, so recovery behavior can be exercised and
  verified reliably. Forcing failures has no effect against a real deployed
  Runtime — it is a local-dev-only mechanism.

### NFR-2: Runtime environment
- Local development and testing run as Python 3.10+ in the VS Code
  environment — no AWS calls required for the in-process path.
- Each agent, once implemented locally, is also deployed as a real Bedrock
  AgentCore Runtime using the lab-owned `AgentRuntimeCodeBucket` and the
  shared `AgentRuntimeExecutionRole` — no other new AWS infrastructure is
  created per agent.
- For the DIY challenge, the Orchestrator reads/writes `AgentCardRegistryTable`
  and `CheckpointTable` directly (both already provisioned) — no new
  DynamoDB tables are created.

### NFR-3: Verifiability
- `main.py` runs end-to-end with no unhandled exception once all 4 agents
  are implemented locally, demonstrating discovery, orchestration + rollback,
  recovery, and context isolation.
- `PARequestResult` exposes `final_state`, `degraded_to_manual_review`,
  `dead_lettered`, and `rollback_count` so recovery/orchestration behavior
  can be checked programmatically, not just read off console output.
- `test_ui.py` (`streamlit run test_ui.py`) tests each agent as soon as it's
  implemented, rather than requiring all 4 before showing anything.
- Each deployed Runtime is verified independently via
  `runtime_client.verify_single_agent(<capability>, <arn>)` before the full
  pipeline is run against all 4 together with `python3 main.py --real`.

## Out of Scope
- Real Nova/foundation model calls in Clinical Extraction, Policy Matching,
  or Decision Synthesis — those stay deterministic and rule-based. Medical
  Necessity's met/not_met judgment is the one exception (NFR-1).
- Model output quality/accuracy evaluation against production targets.
- A UI or API layer in front of the Orchestrator beyond `test_ui.py`.
- Multi-tenant or concurrent-request handling.
- Wiring `AuditTrailTable`, `DeadLetterTable`, or `ManualReviewTable` — those
  three stay as in-memory stand-ins; only `AgentCardRegistryTable` and
  `CheckpointTable` are wired, as the DIY challenge.
- Peer-to-peer agent calls that bypass the Orchestrator, except the one
  Medical Necessity -> Policy Matching call built as the DIY challenge.