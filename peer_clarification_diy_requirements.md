# DIY Challenge: Real Orchestrator State + Peer Clarification

## Overview

The Orchestrator you're given (`orchestrator.py`) is complete and correct —
the state machine, checkpointing, recovery, and context-scoping logic are
reference implementations, not something you build from scratch. The DIY
challenge is to extend that given Orchestrator in two parts, once all 4
agents are deployed as real AgentCore Runtimes and verified:

- **Part 1**: wire the Orchestrator's in-memory `AgentCardRegistry` and
  `CheckpointStore` to the two DynamoDB tables already provisioned for this
  (`AgentCardRegistryTable`, `CheckpointTable`), so registration and
  checkpointing survive an Orchestrator restart instead of living only in
  process memory.
- **Part 2**: add one real peer-to-peer A2A call — Medical Necessity asks
  Policy Matching directly for clarification when evidence is insufficient —
  using Policy Matching's Runtime ARN from `runtime_config_arns.py`, a
  container-local constant filled in after deployment (the Agent Card
  registry has no ARN field, so it cannot resolve an invocable endpoint).

Part 1 and Part 2 are independent — Part 2's peer call does not depend on
Part 1's DynamoDB wiring being done first.

Neither part changes the state machine's transitions, the recovery
semantics, or the context-scoping rules — those stay exactly as given.

## Prerequisite

All 4 agents already implemented, deployed as real AgentCore Runtimes, and
individually verified via `runtime_client.verify_single_agent`, with all 4
ARNs recorded in `runtime_config.py`.

---

## Part 1: DynamoDB-backed registry and checkpointing

### FR-1: Registry backed by `AgentCardRegistryTable`

- `AgentCardRegistry.register()` writes the `AgentCard` to
  `AgentCardRegistryTable` (partition key `agent_name`) instead of only an
  in-memory dict.
- `discover_by_capability()` reads from the table rather than the in-memory
  dict, so a fresh Orchestrator process (no prior `register()` calls in this
  process) can still resolve agents registered by an earlier run.
- `AgentNotFoundError` and `AgentVersionMismatchError` semantics are
  unchanged — they still fire based on what's actually in the table.

**Files and functions touched:** `registry.py` — `AgentCardRegistry.register()`,
`discover_by_capability()`, `is_available()`, `get_card()`. All four read from
or write to `AgentCardRegistryTable`; none of their signatures change.

### FR-2: Checkpointing backed by `CheckpointTable`

- `CheckpointStore.save()` writes the `Checkpoint` to `CheckpointTable`
  (partition key `request_id`) instead of only an in-memory dict.
- `CheckpointStore.load()` reads from the table, so
  `Orchestrator.resume_from_checkpoint()` can resume a pended/in-progress
  request after a restart, not just within the same process.
- `rollback_and_resume()`'s behavior (re-run from `EXTRACTION`, not
  `INTAKE`) is unchanged — only where the checkpoint is read from changes.

**Files and functions touched:** `orchestrator.py` — `Checkpoint` (keep the
`@dataclass` decorator; it's easy to drop while wiring in a table client) and
`CheckpointStore.save()`/`load()`. `Checkpoint` nests other dataclasses with
enum fields, which DynamoDB cannot store directly — serialize enums to their
`.value` on write and reconstruct them on read.

### Out of scope for Part 1

`AuditTrailTable`, `DeadLetterTable`, and `ManualReviewTable` stay as the
in-memory stand-ins already in `orchestrator.py`/`recovery.py` — wiring them
is not part of this challenge.

### Verification for Part 1

Register all 4 agents, then start a fresh Orchestrator process (new Python
process, no prior in-memory state) and confirm `discover_by_capability`
still resolves all 4 without re-registering. Checkpoint a request partway
through the pipeline, restart the process, and confirm
`resume_from_checkpoint` returns the correct last-saved `Checkpoint`.

---

## Part 2: Peer clarification (Medical Necessity -> Policy Matching)

### Overview

Every agent-to-agent interaction in the core pipeline is Orchestrator-mediated — the state machine only transitions via the Orchestrator invoking the next agent, including the PEND to EXTRACTION rollback when Medical Necessity returns "Insufficient Evidence." No agent calls another agent directly.

This feature adds one genuine peer-to-peer A2A interaction: when Medical Necessity flags insufficient evidence, it first asks the Policy Matching agent directly, via Agent Card discovery rather than a hardcoded call, exactly which required document is missing, so the eventual PEND notification is specific rather than generic.

### Remaining prerequisites

Two infrastructure gaps must be closed before Part 2 is buildable, both
pre-provisioned in the CDK stack (`PeerAgentCoreAccess` statement on
`AgentRuntimeExecutionRole`) — a learner's lab role cannot grant itself
`iam:PutRolePolicy`, so neither can be closed by the learner at runtime:

- The Runtime execution role (`AgentRuntimeExecutionRole`) has no
  `GetAgentCard`/`InvokeAgentRuntime` permissions — agents can't call each
  other with the role they're currently given.
- No Runtime's deployment zip includes `boto3` or an equivalent HTTP client
  to make that call from inside the container.

If you're extending this challenge and hit a further permission gap, check
both roles (`AgentRuntimeExecutionRole` for anything a Runtime does to
itself/another Runtime; `ec2_role`/`PriorAuthEc2Role` for anything the
Orchestrator process does directly) before assuming the fix belongs on the
one you already touched.

### Implementation order (do these in this exact sequence)

This DIY has a real dependency chain across 4 files — skipping the order
below is the most common way to get a confusing error several steps later
instead of a clear one immediately.

1. **Deploy Policy Matching's `handle_clarification_request` branch first.**
   Medical Necessity's peer call has nothing to talk to until this side
   exists. Redeploy via `update-agent-runtime` (the Runtime already
   exists, `create-agent-runtime` is wrong here) and confirm it's `READY`.
2. **Fill in `runtime_config_arns.py`'s `POLICY_MATCHING_ARN`** with the
   real deployed ARN from step 1. This is a plain string constant, not a
   registry lookup — `AgentCard` has no ARN field, so nothing in
   `AgentCardRegistryTable` can resolve an invocable endpoint. Do not try
   to look this up via DynamoDB from inside the container; that lookup has
   no ARN to return no matter how it's written.
3. **Implement `peer_client.py`'s `request_clarification()`**, importing
   `POLICY_MATCHING_ARN` from step 2 and calling `InvokeAgentRuntime`
   directly with it.
4. **Add `peer_clarification: dict | None = None`** to both
   `pa_types.py`'s and `agents/medical_necessity_runtime/agent_types.py`'s
   `NecessityDetermination` — these are two separate files (a deployed
   Runtime container can't import across the process boundary) and both
   must be edited, or you'll get a `TypeError` on one side only.
5. **Wire the call into `server.py`'s `agent_function()`**, in the
   `if missing_documents:` branch, importing `request_clarification` and
   `PeerUnavailableError` explicitly from `peer_client` (a bare
   `except PeerUnavailableError:` with no import is a `NameError`, not a
   caught exception).
6. **Rebuild and redeploy Medical Necessity's zip** with `boto3` vendored
   in (`pip install boto3 -t .` inside the Runtime folder before zipping —
   direct code deployment does not run `pip install`), zipped from
   *inside* the folder so `server.py` sits at the zip root, then
   `update-agent-runtime`.
7. **Verify with `test_diy.py`**, which invokes the real deployed Runtime
   with a payload guaranteed to trigger insufficient evidence and checks
   the response's `peer_clarification.requested` field.

### Lessons from building this challenge (for whoever maintains this doc)

Building and verifying Part 2 end-to-end surfaced friction that the
Functional Requirements above don't capture, because it lives in deployment
mechanics rather than agent logic. Three concrete gaps worth closing before
handing this to learners at scale:

1. **The zip-packaging convention is stated once and easy to get wrong.**
   `agent_container_contract.md` says "zip from inside the folder, not the
   folder itself" — but this was still the actual, reproduced mistake
   during implementation (`zip -r x.zip folder/` instead of `cd folder &&
   zip -r ../x.zip .`), producing an opaque `"entrypoint could not be found
   or accessed"` failure with no direct pointer back to the packaging
   convention. Recommend restating this warning directly in whatever
   command a learner is told to run for Part 2 specifically (not just once,
   early, in a different document), or providing a copy-pasteable
   packaging script instead of prose instructions.

2. **Container-local type duplication is a silent-failure trap.** Each
   Runtime's `agent_types.py` is a hand-maintained subset of `pa_types.py`
   (containers can't import across the process boundary), and Part 2 adds
   a *new* field (`NecessityDetermination.peer_clarification`) that must be
   added to the container-local copy too, plus a brand-new
   `runtime_config_arns.py` that must be manually created inside the
   Runtime folder with the peer's ARN. Nothing enforces these stay in sync
   — the failure mode is a `TypeError`/`ModuleNotFoundError` deep inside a
   CloudWatch log, not a clear error at edit time. Recommend either a small
   script that diffs/copies the relevant dataclass fields from `pa_types.py`
   into each Runtime's `agent_types.py`, or restructuring so the
   container-local ARN reference is generated from `runtime_config.py` at
   zip-build time rather than hand-written.

3. **The peer-call fallback is silent by design (FR-2/Out of Scope), which
   makes debugging indistinguishable from success at a glance.** A failed
   peer call and a successful "no clarification needed" case both look
   like unremarkable PEND output unless you specifically check
   `peer_clarification.requested`. During implementation, diagnosing *why*
   the peer call was failing required manually adding a temporary
   `print()` in the `except PeerUnavailableError` block and redeploying —
   a full zip/upload/update-agent-runtime cycle just to see an error
   message. Recommend `peer_client.py` log the `PeerUnavailableError`'s
   message by default (e.g. via `print()`/logging to stdout, which
   CloudWatch captures automatically) rather than swallowing it silently —
   this does not change the required fallback *behavior*, only whether the
   reason for it is visible without a learner needing to add their own
   diagnostic first.

### Functional Requirements

#### FR-1: Clarification trigger

When Medical Necessity's determination is Insufficient Evidence, it attempts exactly one peer clarification call to Policy Matching for this
request, before returning that result to the Orchestrator. A determination of Met or Not Met never triggers a peer call.

#### FR-2: Peer discovery and direct call

Medical Necessity resolves Policy Matching's Runtime ARN from `runtime_config_arns.py` (a container-local constant the learner fills in after deploying Policy Matching), not the Agent Card registry — `AgentCard` has no ARN field, so a DynamoDB-backed lookup cannot resolve an invocable endpoint. The call asks specifically which items in the required documents list correspond to the evidence gap identified, not a re-request of the full list. This call is peer-to-peer and does not go through the Orchestrator's own discovery/invocation logic for this request.

#### FR-3: Context scoping still applies

The peer call from Medical Necessity to Policy Matching respects the same context-scoping rules as the core pipeline. Policy Matching does not
receive clinical notes or patient demographics, even though it is invoked by a peer instead of the Orchestrator.

#### FR-4: Result incorporation and audit

Medical Necessity's Insufficient Evidence response to the Orchestrator includes the specific missing-document list obtained from the peer call. The peer call and its result are written to the audit trail, attributed as agent-initiated rather than Orchestrator-initiated, so compliance review can distinguish the two call patterns. The response includes a peer_clarification field containing requested (boolean), peer_agent, resolved_via, and missing_documents. When no clarification is requested, requested is false and the other fields are omitted or null.

### Out of Scope for Part 2

Changes to the Orchestrator's own state machine or discovery/invocation logic. Peer calls initiated by any agent other than Medical Necessity.
Retrying the peer call on failure — a single failed attempt falls back to the generic PEND behavior.

### Verification for Part 2

A request resulting in Met or Not Met does not trigger a peer call. A request resulting in Insufficient Evidence triggers exactly one peer call to Policy Matching, resolved via the Agent Card registry. The resulting PEND notification includes the specific missing-document list, not the full required documents list. The peer call does not include clinical notes or patient demographics. The peer call and its result appear in the audit trail, attributed as agent-initiated. If Policy Matching is unavailable when the peer call is attempted, Medical Necessity falls back to the generic PEND behavior rather than failing the whole request.
