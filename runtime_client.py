"""
Bridges registry.py's AgentCardRegistry to real deployed AgentCore Runtimes.
build_real_agent_registry() populates a registry in the same shape as
in-process registration, except each invoke_fn performs a real
InvokeAgentRuntime call instead of calling a local Python function.

Discovery stays real, not a lookup table: runtime_config.AGENT_RUNTIME_ARNS
only says where to call GetAgentCard — the registered AgentCard comes from
each Runtime's own response, so a wrong ARN-to-key pairing fails loudly here
rather than silently misrouting a request.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Callable, get_type_hints

try:
    import boto3
except ImportError as exc:  # pragma: no cover - environment-dependent
    raise ImportError(
        "runtime_client.py requires boto3 to call real AgentCore Runtimes. "
        "Install it: pip install boto3."
    ) from exc

from pa_types import (AgentCard, ClinicalSummary, FinalDetermination,
                      NecessityDetermination, PolicyMatch)
from registry import AgentCardRegistry, AgentNotFoundError

# bedrock-agentcore requires 33-256 character runtimeSessionId values.
_MIN_SESSION_ID_LEN = 33

# Maps each capability to the dataclass its agent returns, so a real
# InvokeAgentRuntime call's JSON response is reconstructed into the same
# dataclass type the in-process path returns — orchestrator.py accesses
# fields like clinical_summary.notes_summary as attributes either way, and
# should not need to know whether it's talking to a local function or a
# real deployed Runtime.
_OUTPUT_TYPE_BY_CAPABILITY: dict[str, type] = {
    "clinical_extraction": ClinicalSummary,
    "policy_matching": PolicyMatch,
    "medical_necessity": NecessityDetermination,
    "decision_synthesis": FinalDetermination,
}


class RuntimeDiscoveryError(Exception):
    """A configured Runtime ARN doesn't advertise the capability it was
    registered under, or GetAgentCard/InvokeAgentRuntime failed."""


def _read_streaming_body(value: Any) -> Any:
    # boto3 returns some fields as a StreamingBody, others as already-parsed
    # dicts depending on botocore version — handle both.
    if hasattr(value, "read"):
        value = value.read()
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        value = json.loads(value)
    return value


def _agent_card_from_response(
    capability_key: str, runtime_arn: str, raw_card: dict[str, Any]
) -> AgentCard:
    skills = raw_card.get("skills", [])
    capabilities = [skill["id"] for skill in skills if "id" in skill]
    if capability_key not in capabilities:
        raise RuntimeDiscoveryError(
            f"Runtime {runtime_arn} was configured under capability '{capability_key}' in "
            f"runtime_config.py, but its real GetAgentCard response advertises "
            f"{capabilities!r} instead. Fix the ARN-to-key pairing in runtime_config.py."
        )
    return AgentCard(
        agent_name=raw_card.get("name", capability_key),
        capabilities=capabilities,
        input_schema={},
        output_schema={},
        version=raw_card.get("version", "1.0"),
    )


def _reconstruct_dataclass(output_type: type, raw: dict[str, Any]) -> Any:
    # A real Runtime's response is plain JSON, so any enum-typed field
    # (e.g. NecessityDetermination.result, FinalDetermination.decision)
    # arrives as its raw string value, not the enum instance the in-process
    # path would produce. Coerce those fields back to the declared enum type
    # so callers can use `.value` or `==` against the enum either way,
    # matching the in-process contract exactly.
    #
    # pa_types.py uses `from __future__ import annotations`, so
    # dataclasses.fields() reports each field's type as an unresolved string
    # (e.g. "NecessityResult") rather than the class itself — get_type_hints
    # resolves those strings back to real types.
    resolved_types = get_type_hints(output_type)
    coerced = dict(raw)
    for field_name, field_type in resolved_types.items():
        if field_name in coerced and isinstance(field_type, type) and issubclass(
            field_type, Enum
        ):
            coerced[field_name] = field_type(coerced[field_name])
    return output_type(**coerced)


def _make_invoke_fn(runtime_arn: str, output_type: type) -> Callable[..., Any]:
    # Matches orchestrator.py's calling convention: clinical_extraction is
    # called with two positional args (request, failures); every other agent
    # is called kwargs-only. `failures` is never sent over the wire.
    client = boto3.client("bedrock-agentcore")

    def invoke(*args: Any, **kwargs: Any) -> Any:
        kwargs.pop("failures", None)
        if args:
            request = args[0]
            scoped_input = asdict(request) if is_dataclass(request) else dict(request)
        else:
            scoped_input = {
                k: (asdict(v) if is_dataclass(v) else v) for k, v in kwargs.items()
            }

        session_id = str(uuid.uuid4())
        assert len(session_id) >= _MIN_SESSION_ID_LEN
        rpc_id = str(uuid.uuid4())
        rpc_request = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "message/send",
            "params": {
                "message": {
                    "parts": [{"kind": "text", "text": json.dumps(scoped_input)}]
                }
            },
        }

        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(rpc_request).encode("utf-8"),
        )
        rpc_response = _read_streaming_body(response.get("response", response))

        if "error" in rpc_response:
            error = rpc_response["error"]
            raise RuntimeDiscoveryError(
                f"Runtime {runtime_arn} returned a JSON-RPC error: "
                f"{error.get('code')} {error.get('message')}"
            )

        output_text = rpc_response["result"]["artifacts"][0]["parts"][0]["text"]
        output = json.loads(output_text)
        # A None result (e.g. Policy Matching's "no match") serializes as the
        # JSON literal null — preserve that instead of coercing it into an
        # empty/invalid instance of output_type.
        if output is None:
            return None
        return _reconstruct_dataclass(output_type, output)

    return invoke


def build_real_agent_registry(agent_runtime_arns: dict[str, str]) -> AgentCardRegistry:
    # Entries with an empty ARN are skipped, so agents can be deployed and
    # registered incrementally rather than all at once.
    client = boto3.client("bedrock-agentcore")
    registry = AgentCardRegistry()

    for capability_key, runtime_arn in agent_runtime_arns.items():
        if not runtime_arn:
            continue

        session_id = str(uuid.uuid4())
        assert len(session_id) >= _MIN_SESSION_ID_LEN
        try:
            response = client.get_agent_card(
                agentRuntimeArn=runtime_arn, runtimeSessionId=session_id
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 - surface the real AWS-side failure, don't mask it
            raise RuntimeDiscoveryError(
                f"GetAgentCard failed for {capability_key} ({runtime_arn}): {exc}"
            ) from exc

        raw_card = _read_streaming_body(response.get("agentCard", response))
        card = _agent_card_from_response(capability_key, runtime_arn, raw_card)
        output_type = _OUTPUT_TYPE_BY_CAPABILITY[capability_key]
        registry.register(card, _make_invoke_fn(runtime_arn, output_type))

    return registry


def require_all_registered(
    registry: AgentCardRegistry, capabilities: list[str]
) -> None:
    missing = []
    for capability in capabilities:
        try:
            registry.discover_by_capability(capability)
        except AgentNotFoundError:
            missing.append(capability)
    if missing:
        raise AgentNotFoundError(
            f"No Runtime registered for capabilities: {missing}. Fill in the corresponding "
            f"ARN(s) in resources/runtime_config.py after deploying those Runtimes."
        )


# A realistic unscoped sample request, used by verify_single_agent() to run
# the same scope_for_*/assert_isolation checks orchestrator.py runs before a
# real invocation, proving isolation holds for one agent at a time.
_SAMPLE_UNSCOPED_REQUEST = {
    "request_id": "PA-VERIFY",
    "patient_name": "Verify Patient",
    "patient_dob": "1990-01-01",
    "subscriber_id": "SUB-VERIFY",
    "plan_id": "PLN-VERIFY",
    "line_of_business": "commercial",
    "procedure_codes": ["80053"],
    "diagnosis_codes": ["Z00.00"],
    "clinical_notes": "Routine verification note for incremental Runtime testing.",
    "billing_info": {"copay": 20},
}

# Scoped-input samples for agents downstream of clinical_extraction/policy_matching.
_SAMPLE_SCOPED_INPUTS: dict[str, dict[str, Any]] = {
    "policy_matching": {
        "procedure_codes": ["80053"],
        "plan_id": "PLN-VERIFY",
        "line_of_business": "commercial",
        "request_id": "PA-VERIFY",
    },
    "medical_necessity": {
        "notes_summary": "Verification note.",
        "coverage_criteria": ["Ordered by in-network provider"],
        "required_docs_list": ["imaging_report"],
        "request_id": "PA-VERIFY",
    },
    "decision_synthesis": {
        "necessity_result": "met",
        "evidence_citations": ["Verification citation."],
        "missing_evidence": [],
        "policy_id": "POL-VERIFY",
        "request_id": "PA-VERIFY",
    },
}


def verify_single_agent(capability: str, runtime_arn: str) -> Any:
    """Verifies one agent's Runtime without requiring the other 3 to exist.
    Runs the same scope_for_*/assert_isolation + discover_by_capability +
    invoke_fn sequence the Orchestrator uses, for just this one agent.
    """
    from context import (FORBIDDEN_FOR_DECISION_SYNTHESIS,
                         FORBIDDEN_FOR_MEDICAL_NECESSITY,
                         FORBIDDEN_FOR_POLICY_MATCHING, assert_isolation,
                         scope_for_decision_synthesis,
                         scope_for_medical_necessity,
                         scope_for_policy_matching)
    from pa_types import PARequest

    registry = build_real_agent_registry({capability: runtime_arn})
    agent = registry.discover_by_capability(capability)

    if capability == "clinical_extraction":
        # Receives the full, unscoped request — the one agent Table 5 exempts
        # from scoping, since it's first in the pipeline.
        request = PARequest(**_SAMPLE_UNSCOPED_REQUEST)
        return agent.invoke_fn(request, None)

    if capability == "policy_matching":
        request = PARequest(**_SAMPLE_UNSCOPED_REQUEST)
        scoped = scope_for_policy_matching(request)
        assert_isolation(
            scoped, FORBIDDEN_FOR_POLICY_MATCHING, agent_name="policy-matching"
        )
        return agent.invoke_fn(**scoped, request_id=request.request_id)

    if capability == "medical_necessity":
        sample = _SAMPLE_SCOPED_INPUTS["medical_necessity"]
        scoped = scope_for_medical_necessity(
            notes_summary=sample["notes_summary"],
            coverage_criteria=sample["coverage_criteria"],
            required_docs_list=sample["required_docs_list"],
        )
        assert_isolation(
            scoped, FORBIDDEN_FOR_MEDICAL_NECESSITY, agent_name="medical-necessity"
        )
        return agent.invoke_fn(**scoped, request_id=sample["request_id"])

    if capability == "decision_synthesis":
        sample = _SAMPLE_SCOPED_INPUTS["decision_synthesis"]
        scoped = scope_for_decision_synthesis(
            necessity_result=sample["necessity_result"],
            evidence_citations=sample["evidence_citations"],
            missing_evidence=sample["missing_evidence"],
            policy_id=sample["policy_id"],
        )
        assert_isolation(
            scoped, FORBIDDEN_FOR_DECISION_SYNTHESIS, agent_name="decision-synthesis"
        )
        return agent.invoke_fn(**scoped, request_id=sample["request_id"])

    raise ValueError(
        f"Unknown capability '{capability}'. Expected one of: clinical_extraction, "
        f"policy_matching, medical_necessity, decision_synthesis."
    )
