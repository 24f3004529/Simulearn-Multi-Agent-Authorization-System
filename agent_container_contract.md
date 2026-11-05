# Agent Container Contract (A2A / AgentCore Runtime)

Every agent is deployed as its own real Amazon Bedrock AgentCore Runtime, implementing this exact A2A contract. This is what makes real
`GetAgentCard` and `InvokeAgentRuntime` calls work against each Runtime, instead of an in-process function call.

## 1. Why A2A, not plain HTTP

AgentCore Runtime supports several wire protocols (HTTP, MCP, A2A, AG-UI). Only A2A has a built-in Agent Card discovery endpoint. Since the
Orchestrator discovers every agent via `GetAgentCard` rather than a hardcoded reference, every agent Runtime speaks A2A.

## 2. Container requirements

- Host: `0.0.0.0`, Port: `9000` (A2A's fixed port).
- Platform: ARM64 for container image deployments; for direct code deployment this only matters if dependencies include compiled/native extensions.
- Framework: any HTTP server capable of JSON-RPC 2.0. Every agent here uses only the Python standard library (`http.server`), since direct code deployment does not run `pip install`.

## 3. Required endpoints

### `POST /`

Receives a JSON-RPC 2.0 request, returns a JSON-RPC 2.0 response. `bedrock-agentcore:InvokeAgentRuntime` forwards this as a complete pass-through; the Orchestrator builds the envelope.

Request shape: `{"jsonrpc": "2.0", "id": "req-001", "method": "message/send", "params": {"message": {"role": "user", "parts": [{"kind": "text","text": "<JSON-encoded scoped input>"}], "messageId": "unique-message-id"}}}`

Response shape: `{"jsonrpc": "2.0", "id": "req-001", "result": {"artifacts": [{"artifactId": "unique-artifact-id", "name": "agent_response",
"parts": [{"kind": "text", "text": "<JSON-encoded output dataclass>"}]}]}}`

The `text` field convention (local, since A2A's base spec has no structured-data part type): the Orchestrator JSON-serializes the scoped dict from `context.py`'s `scope_for_*` into `params.message.parts[0].text`; the handler JSON-serializes its output dataclass (`asdict()` + `json.dumps`, enums as `.value`) into `result.artifacts[0].parts[0].text`. A `None` result serializes as the JSON literal `null`.

### `GET /.well-known/agent-card.json`

Returns the agent's card as plain JSON — what `GetAgentCard` fetches for the Runtime ARN. Maps from `pa_types.AgentCard`: `agent_name` → `name`, `version` → `version`, each `capabilities` entry → one `skills[]` entry (`id`/`name` both equal to the capability string),
`input_schema`/`output_schema` → folded into that skill's `description`.

### `GET /ping`

Returns `{"status": "Healthy"}` or `{"status": "HealthyBusy"}`. No `time_of_last_update` unless reporting an actual status change — setting it every call breaks AgentCore's idle-session timeout.

## 4. Error handling

JSON-RPC errors in the `error` field: malformed/missing input → code `-32052`, HTTP 400. An agent failure (e.g. `TransientAgentError`) → code
`-32603`, HTTP 500. Throttling/conflicts are handled by AgentCore itself.

## 5. What does not change

Agent logic is unchanged from the in-memory design — same function signature, `failures.maybe_fail(...)` call, return type. `context.py`'s
`scope_for_*`/`assert_isolation` still run in the Orchestrator before the request is built. `recovery.py`'s per-agent circuit breaker still wraps every call, now around a real `InvokeAgentRuntime` call.

## 6. Per-agent status and deployment

| Agent | Concept | Status | Notes |
|---|---|---|---|
| Policy Matching | Lookup with absence handling | Given | Deploy `agents/policy_matching_runtime/` as-is. |
| Medical Necessity | Ternary rule evaluation + Nova 2 Lite call | Given | Deploy `agents/medical_necessity_runtime/` as-is. |
| Clinical Extraction | Input normalization | Scaffold | Fill in `agents/clinical_extraction_runtime/server.py`. |
| Decision Synthesis | Aggregation and explanation | Scaffold | Fill in `agents/decision_synthesis_runtime/server.py`; cross-check its `runtime_config_notes.md` against
`runtime_config.py`'s own docstring rather than trusting it directly. |

Each agent's Concept is what its practice targets — see `requirements.md`
FR-5. All 4 share the same container contract above regardless of concept;
the difference is only in each agent's own logic, not in how it's deployed.

Each Runtime folder has the same shape: `a2a_server_boilerplate.py` and `failure_injector.py` copied unchanged, `agent_types.py` (this agent's
subset of `pa_types.py`), `synthetic_data.py` (only if needed), and `server.py` (this agent's `*_CARD`, agent function, and `handle_request`, ending with `run_server(CARD, DESCRIPTION, handle_request, validation_error_cls=KeyError, agent_error_cls=TransientAgentError)`).

Three fixed deployment constraints: zip from inside the folder so `server.py` sits at the zip root; `create-agent-runtime` requires
`--protocol-configuration '{"serverProtocol": "A2A"}'` explicitly (it defaults to `HTTP`); `runtimeSessionId` must be 33-256 characters.

Deployment steps: zip and upload to `AgentRuntimeCodeBucket`; create the Runtime with `codeConfiguration`, `PYTHON_3_12`, entrypoint
`["server.py"]`, the A2A protocol configuration, and `AgentRuntimeExecutionRole`; poll `get-agent-runtime` until `READY` (read `failureReason` on `CREATE_FAILED`); call `runtime_client.verify_single_agent(<capability>, <arn>)` for that one agent independently; once all 4 pass, record every ARN in `runtime_config.py` and run `python3 main.py --real`.

## 7. DynamoDB-backed registry (DIY)

Once all 4 Runtimes are deployed and verified, the DIY challenge wires
`AgentCardRegistry` to `AgentCardRegistryTable` (replacing its in-memory
dict) so a Runtime's registered card survives an Orchestrator restart. This
is an Orchestrator-side change, not a per-agent container change — no agent's
`server.py` or deployment zip is affected. See
`peer_clarification_diy_requirements.md` for the full DIY scope, which also
covers `CheckpointTable` and the Medical Necessity peer call.