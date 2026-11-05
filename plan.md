# Plan: Real Orchestrator State + Peer Clarification (DIY)

Source: `peer_clarification_diy_requirements.md`.
Goal: (Part 1) back `AgentCardRegistry` and `CheckpointStore` with DynamoDB so state survives an Orchestrator restart; (Part 2) add one real peer-to-peer A2A call from Medical Necessity to Policy Matching when evidence is insufficient.

**Ground rules that hold for the whole plan**
- Don't change the state machine transitions, recovery semantics (`recovery.py`), or context-scoping rules (`context.py`).
- Don't change any public signatures in `registry.py` / `CheckpointStore`.
- `AuditTrailTable`, `DeadLetterTable`, `ManualReviewTable` stay in memory.
- Part 1 and Part 2 are independent. They can be built in either order or in parallel.

---

## 0. Prerequisite check (before any code)

| Check | How |
|---|---|
| All 4 Runtimes deployed and `READY` | `aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id <id>` for each |
| All 4 ARNs recorded | `runtime_config.py` already has all 4 filled in ✅ |
| Each agent verified on its own | `python3 -c "from runtime_client import verify_single_agent; ..."` for each capability |
| Real table names | `aws dynamodb list-tables`. The CDK stack may add a suffix to the names, so don't assume the exact strings `AgentCardRegistryTable` / `CheckpointTable` |
| Table key schema | `aws dynamodb describe-table`: expect PK `agent_name` (registry) and `request_id` (checkpoint), with no sort key |
| EC2/Orchestrator role (`PriorAuthEc2Role`) can `GetItem/PutItem/Scan` on both tables | Try a one-off `put-item` and `scan` from the Orchestrator host |
| `AgentRuntimeExecutionRole` has the `PeerAgentCoreAccess` statement (`InvokeAgentRuntime`, `GetAgentCard`) | `aws iam get-role-policy` / `list-attached-role-policies`. This is pre-provisioned; if it's missing, escalate (a lab role can't self-grant `iam:PutRolePolicy`) |

---

## Part 1: DynamoDB-backed registry and checkpointing

### 1.1 Shared config: table names

Add a small module `dynamo_config.py` at the repo root:

```python
import os
AGENT_CARD_REGISTRY_TABLE = os.environ.get("AGENT_CARD_REGISTRY_TABLE", "AgentCardRegistryTable")
CHECKPOINT_TABLE = os.environ.get("CHECKPOINT_TABLE", "CheckpointTable")
```

Environment variables can override the defaults to match whatever names the CDK stack actually created (step 0). Get the table with `boto3.resource("dynamodb").Table(...)`, created lazily on first use so that importing the module never makes a network call.

### 1.2 FR-1: `registry.py`, backed by `AgentCardRegistryTable`

**Design problem to solve:** `RegisteredAgent` holds an `invoke_fn` callable, which can't be stored in DynamoDB. A fresh process (with no `register()` calls) still has to return a `RegisteredAgent` from `discover_by_capability()`.

**Approach**
- Store only the `AgentCard` in the table:
  `{agent_name, capabilities (list), input_schema (map), output_schema (map), version}`.
- Keep `self._agents` as a **local cache of invoke functions only**, not as the source of truth for what is registered.
- Add an optional constructor parameter `invoke_fn_resolver: Callable[[AgentCard], Callable] | None = None`. The signatures of the four FR-1 methods don't change. When a card is read from the table and there is no cached `invoke_fn` for it:
  1. use `invoke_fn_resolver(card)` if one was given. `main.build_real_orchestrator()` can pass a resolver that maps capability → ARN (`runtime_config.AGENT_RUNTIME_ARNS`) → `runtime_client._make_invoke_fn(...)`.
  2. otherwise attach a placeholder `invoke_fn` that raises a clear `AgentNotFoundError("card found in table but no invoker bound in this process")` when it's called.
  This keeps `test_diy.py`'s fresh-process `discover_by_capability()` working. That test only discovers, it never invokes.
- Keep the import one-way, from `main.py` into `registry.py`, so there's no `registry` ↔ `runtime_client` circular import.

**Method changes**
| Method | Change |
|---|---|
| `register(card, invoke_fn)` | `table.put_item(Item=_card_to_item(card))`, then cache `invoke_fn` locally |
| `discover_by_capability(capability, min_version)` | `table.scan(FilterExpression=Attr("capabilities").contains(capability))`, following `LastEvaluatedKey` pagination → turn each item back into an `AgentCard` → **same** `AgentNotFoundError` / `AgentVersionMismatchError` logic as today, run on the table results |
| `is_available(agent_name)` | `table.get_item(Key={"agent_name": ...})` → `"Item" in resp` |
| `get_card(agent_name)` | `get_item`; raise `AgentNotFoundError` if it's missing |

Notes:
- Read with `ConsistentRead=True` so a register followed by an immediate discover in the same process is deterministic.
- Make the order of candidates deterministic (sort by `agent_name`) so "first match" is stable. A scan has no guaranteed order.
- Wrap `ClientError` with context (table name, operation), but **don't** turn AWS errors into `AgentNotFoundError`. A permissions failure must not look like "agent not registered".
- Re-registering the same `agent_name` overwrites the item, which matches the old dict behaviour.

### 1.3 FR-2: `orchestrator.py`, backed by `CheckpointTable`

- Keep `@dataclass` on `Checkpoint`. (The requirements doc calls this out as easy to drop by accident.)
- Add serialization helpers (in `orchestrator.py` or a small `checkpoint_codec.py`):
  - **Write:** `dataclasses.asdict(checkpoint)` → walk it recursively, turning `Enum` into `.value` and leaving `None` as is (DynamoDB accepts NULL). Store the nested dataclasses as maps.
  - **Read:** rebuild each type explicitly:
    - `state` → `PAState(item["state"])`
    - `clinical_summary` → `ClinicalSummary(**...)`
    - `policy_match` → `PolicyMatch(**...)`
    - `necessity` → `NecessityDetermination(**{..., "result": NecessityResult(...)})`. This includes `peer_clarification` once Part 2 adds it, and tolerates the field being absent on old items.
    - `final` → `FinalDetermination(**{..., "decision": Decision(...)})`
  - Avoid empty string sets and floats. All current fields are str, list, dict or enum, so plain `put_item` works. If a float ever appears, convert it to `Decimal`.
- `CheckpointStore.save()` → `put_item` (last write wins, one item per `request_id`). It can keep the in-memory dict as a write-through cache.
- `CheckpointStore.load()` → `get_item(ConsistentRead=True)`, then decode, or return `None`.
- `resume_from_checkpoint()` and `rollback_and_resume()` stay unchanged. They already go through `self.checkpoints.load()`.

### 1.4 Wiring in `main.py`

- `build_orchestrator()` (local path): unchanged apart from the registry now writing to the table.
- `build_real_orchestrator()`: pass the `invoke_fn_resolver` described in 1.2 so a restarted process can invoke agents without calling `GetAgentCard` again.
- Optional: a `--local-state` flag or env var to keep the pure in-memory behaviour for offline development.

### 1.5 Verification (Part 1)

1. `python3 test_diy.py`: the two Part 1 checks are cross-process (registry discovery of all 4 `EXPECTED_CAPABILITIES`, and an enum-aware checkpoint round-trip).
2. Manual run: `python3 main.py --real` registers the 4 agents → new process → `discover_by_capability()` for all 4 without re-registering.
3. Manual run: drive a request to `MED_NECESSITY` / `PEND` → restart → `resume_from_checkpoint(request_id)` returns the last saved state with real enum instances.
4. Clean up the test items (`test-diy-verify-agent-*`, `TEST-DIY-VERIFY-001`) afterwards if graders don't need them. Check first: the grader's `REGISTRY_MISSING_CAPABILITIES` check reads the table.

---

## Part 2: Peer clarification (Medical Necessity → Policy Matching)

Follow the doc's **exact order**. Each step depends on the one before it.

### Step 1: Policy Matching: `handle_clarification_request` (deploy first)

File: `agents/policy_matching_runtime/server.py`
- Add `handle_clarification_request(scoped_input)`:
  - Input (already scoped): `required_docs_list`, `missing_documents`, `request_id`.
  - Logic: return only the items from `required_docs_list` that match the identified gap, i.e. `[d for d in required_docs_list if d in set(missing_documents)]` (order kept, no duplicates). Don't re-send the full list.
  - Output: a plain dict, e.g. `{"request_id", "missing_documents": [...], "resolved_by": "policy-matching"}`. The boilerplate's `_to_jsonable` sends it as-is.
  - Defensive check: if the payload contains any key in `FORBIDDEN_FOR_POLICY_MATCHING` (`clinical_notes`, `patient_name`, `patient_dob`, `diagnosis_codes`), raise `KeyError`. This enforces FR-3 on the receiving side and returns a validation error.
- In `handle_request`: `if scoped_input.get("clarification_request"): return handle_clarification_request(scoped_input)` **before** reading `procedure_codes`, which would otherwise `KeyError`.
- Package and redeploy (stdlib only, no vendoring needed):
  ```bash
  cd agents/policy_matching_runtime && zip -r ../policy_matching_runtime.zip . -x '__pycache__/*' && cd -
  ```
  Upload to `AgentRuntimeCodeBucket` → **`update-agent-runtime`** (not `create-…`) → poll until `READY`.
- Run `verify_single_agent("policy_matching", ...)` again so you know the normal path didn't regress.

### Step 2: `runtime_config_arns.py`

File: `agents/medical_necessity_runtime/runtime_config_arns.py`
- `POLICY_MATCHING_ARN = "arn:aws:bedrock-agentcore:us-east-1:301463937497:runtime/policy_matching-uIfcHcFdPa"` (from `runtime_config.py`; confirm it hasn't changed after step 1).
- This is a plain constant. Don't add a DynamoDB lookup, because `AgentCard` has no ARN field.
- (Improvement, Lesson #2) Add `scripts/gen_runtime_config_arns.py` that writes this file from `runtime_config.AGENT_RUNTIME_ARNS["policy_matching"]` at zip-build time, so it can't drift.

### Step 3: `peer_client.request_clarification()`

File: `agents/medical_necessity_runtime/peer_client.py`
- If `POLICY_MATCHING_ARN` is blank → raise `PeerUnavailableError("POLICY_MATCHING_ARN not configured")`.
- Build the **scoped** payload (FR-3): `{"clarification_request": True, "required_docs_list", "missing_documents", "request_id"}`. No notes, summary, demographics or coverage criteria.
- Wrap it in the same JSON-RPC `message/send` envelope that `runtime_client._make_invoke_fn` uses. Use a `runtimeSessionId` from `uuid4()` (36 chars ≥ 33).
- `boto3.client("bedrock-agentcore").invoke_agent_runtime(...)` → read the streaming body → if there's a JSON-RPC `error`, raise `PeerUnavailableError` → parse `result.artifacts[0].parts[0].text` → return `missing_documents`.
- Catch **all** exceptions (botocore `ClientError`, `BotoCoreError`, JSON/KeyError) → `raise PeerUnavailableError(str(exc)) from exc`.
- **One attempt only, no retry** (out of scope). Set a short botocore `Config(read_timeout=..., retries={"max_attempts": 1})` so a slow peer can't hang Medical Necessity.
- (Lesson #3) `print(f"[peer_client] PeerUnavailableError: {msg}", flush=True)` before raising, so the reason shows up in CloudWatch without a debug redeploy. Fallback behaviour stays the same.

### Step 4: `NecessityDetermination.peer_clarification`, in both copies

- `pa_types.py`: add `peer_clarification: dict | None = None` **to the existing class** (don't define the class a second time) and remove the TODO.
- `agents/medical_necessity_runtime/agent_types.py`: the same field.
- Without the `pa_types.py` field, `runtime_client._reconstruct_dataclass` raises `TypeError: unexpected keyword 'peer_clarification'` on the Orchestrator side. Without the container field, `server.py` raises.
- (Improvement, Lesson #2) Add `scripts/check_agent_types_sync.py`, which compares the dataclass fields in each `agents/*_runtime/agent_types.py` against `pa_types.py` and exits non-zero on drift. Run it before every zip.

### Step 5: Wire it into Medical Necessity `server.py`

File: `agents/medical_necessity_runtime/server.py`
- `from peer_client import PeerUnavailableError, request_clarification` (both names imported explicitly).
- In the `if missing_documents:` branch (FR-1: this is the only branch that triggers a call, exactly once):
  ```python
  try:
      confirmed = request_clarification(
          required_docs_list=required_docs_list,
          missing_documents=missing_documents,
          request_id=request_id,
      )
      peer_clarification = {
          "requested": True,
          "peer_agent": "policy-matching",
          "resolved_via": "runtime_config_arns.POLICY_MATCHING_ARN",
          "initiated_by": "agent:medical-necessity",
          "missing_documents": confirmed,
      }
      missing_evidence = confirmed or missing_documents
  except PeerUnavailableError:
      peer_clarification = {"requested": False, "peer_agent": None,
                            "resolved_via": None, "missing_documents": None}
      missing_evidence = missing_documents   # generic PEND fallback
  ```
  Then return `NecessityDetermination(..., missing_evidence=missing_evidence, peer_clarification=peer_clarification)`.
- Met / Not Met paths: `peer_clarification={"requested": False}` (or `None`). No peer call.
- Open question to resolve: when the peer call **fails**, should `requested` be `True` (we tried) or `False`? FR-4 says it's `false` "when no clarification is requested". Recommendation: `requested: True`, `missing_documents: None`, plus an `"error"` field, so a failure can be told apart from "not needed" (Lesson #3). Also check this against what `test_diy.py` expects: it only checks the success path.
- Also mirror this in the in-process `agents/medical_necessity_agent.py` (local `main.py` path) using a stub or an in-process peer call, so local runs and tests produce the same field shape. The stub should go through the registry's `policy_matching` agent, not a hardcoded import.

### Step 5b: Orchestrator audit (FR-4)

File: `orchestrator.py`. This fills in the existing `TODO(DIY Part 2)`. It is audit-only, with no state machine change.
- After the `MED_NECESSITY` audit record and before the `INSUFFICIENT_EVIDENCE` early return:
  ```python
  pc = getattr(necessity, "peer_clarification", None)
  if pc and pc.get("requested"):
      self.audit.record(request.request_id, "PEER_CLARIFICATION",
          {"initiated_by": "agent", "caller": "medical-necessity",
           "peer_agent": pc.get("peer_agent"), "resolved_via": pc.get("resolved_via"),
           "missing_documents": pc.get("missing_documents")})
  ```
- The PEND audit's `missing_evidence` then automatically carries the peer-confirmed, specific list.
- Audit entries for calls the Orchestrator makes itself keep their existing shape. Optionally add `"initiated_by": "orchestrator"` so the two kinds of call can be told apart without guessing.

### Step 6: Rebuild and redeploy the Medical Necessity zip

`boto3` is already vendored in `agents/medical_necessity_runtime/` (it's already used for the Nova call). Refresh it if needed, then zip **from inside** the folder.

Add `scripts/package_runtime.sh <runtime_folder>` (Lesson #1) so learners don't have to type this by hand:
```bash
#!/usr/bin/env bash
set -euo pipefail
dir="agents/$1"; out="$(pwd)/agents/$1.zip"
python3 scripts/check_agent_types_sync.py
[ "$1" = medical_necessity_runtime ] && pip install boto3 -t "$dir" --upgrade -q
rm -f "$out"
( cd "$dir" && zip -rq "$out" . -x '__pycache__/*' '*/__pycache__/*' )   # zip from INSIDE the folder
unzip -l "$out" | awk '{print $4}' | grep -qx server.py \
  || { echo "server.py is not at the zip root"; exit 1; }
```
Then upload → `update-agent-runtime` → poll until `READY` (read `failureReason` if it fails).

### Step 7: Verify

- `python3 test_diy.py`: the Part 2 check calls the deployed Medical Necessity Runtime with the `PA-VERIFY` sample (`required_docs_list=["imaging_report"]`, nothing attached). It expects `peer_clarification.requested == True` and a non-empty `missing_documents` (`["imaging_report"]`).
- Check the remaining "Verification for Part 2" items by hand:

| Requirement | How to check |
|---|---|
| Met / Not Met → no peer call | `main.py --real` with PA-0003-style complete docs → `peer_clarification.requested` is false; no peer log line in CloudWatch |
| Insufficient → exactly one peer call | Policy Matching CloudWatch shows one `clarification_request` invocation per request |
| PEND lists the specific docs, not the full list | Use the knee policy (3 required docs) with PA-0002 (1 attached) → PEND `missing_evidence` contains only the 2 missing docs |
| No clinical notes or demographics in the peer call | Log the payload keys in `handle_clarification_request`; the receiver-side check rejects forbidden keys |
| Audit attributes the call to the agent | `orchestrator.audit.entries` contains a `PEER_CLARIFICATION` entry with `initiated_by: "agent"` |
| Peer unavailable → generic PEND, no failure | Temporarily set a bogus `POLICY_MATCHING_ARN` (or blank it) and redeploy → request still ends in PEND with the full missing list, and `[peer_client]` error appears in CloudWatch |

---

## Spec inconsistencies to flag

1. **"resolved via the Agent Card registry"** (Part 2 Overview and Verification) contradicts FR-2 and the implementation order, which say to resolve the ARN from `runtime_config_arns.py` because `AgentCard` has no ARN field. The plan follows FR-2. Set `resolved_via` honestly to the ARN constant, and ask the doc maintainers to reword the Verification line.
2. **`requested` on a failed peer call.** FR-4 doesn't say what to do here (see Step 5). The plan's recommendation keeps "attempted but failed" visible.
3. **Registry `invoke_fn` in a fresh process.** FR-1 asks for discovery from the table but doesn't say how a callable is recovered. The plan uses a resolver plus a placeholder (1.2).

## Deliverables / files touched

| File | Part | Change |
|---|---|---|
| `dynamo_config.py` (new) | 1 | Table names (env-overridable) |
| `registry.py` | 1 | Table-backed `register` / `discover_by_capability` / `is_available` / `get_card`; optional resolver |
| `orchestrator.py` | 1 + 2 | `CheckpointStore` table-backed + enum codec; `PEER_CLARIFICATION` audit record |
| `main.py` | 1 | Pass the invoke-fn resolver in the real path |
| `pa_types.py` | 2 | `NecessityDetermination.peer_clarification` |
| `agents/medical_necessity_runtime/agent_types.py` | 2 | Same field |
| `agents/medical_necessity_runtime/runtime_config_arns.py` | 2 | Real `POLICY_MATCHING_ARN` |
| `agents/medical_necessity_runtime/peer_client.py` | 2 | `request_clarification()` + logging |
| `agents/medical_necessity_runtime/server.py` | 2 | Peer call in the `missing_documents` branch |
| `agents/medical_necessity_agent.py` | 2 | Same field shape for the local path |
| `agents/policy_matching_runtime/server.py` | 2 | `handle_clarification_request` + dispatch |
| `scripts/package_runtime.sh`, `scripts/check_agent_types_sync.py`, `scripts/gen_runtime_config_arns.py` (new) | 2 | Fixes for the "Lessons learned" items |

## Suggested milestones

1. Prerequisite checks (step 0)
2. Part 1 registry → `test_diy.py` registry check passes
3. Part 1 checkpoint → `test_diy.py` checkpoint check passes
4. Part 2 steps 1–2 (Policy Matching deployed, ARN set)
5. Part 2 steps 3–5b (code + audit), local `main.py` run is green
6. Part 2 steps 6–7 (Medical Necessity redeployed) → `test_diy.py` 3/3, plus the manual verification table
