"""
DIY Part 2 — container-local A2A client for Medical Necessity's peer call to
Policy Matching. This call is agent-initiated and peer-to-peer: it goes
straight to Policy Matching's Runtime via InvokeAgentRuntime, not through the
Orchestrator's discovery/invocation logic.

Exactly one attempt is made (no retries); any failure is raised as
PeerUnavailableError so the caller falls back to the generic PEND behavior.
The failure reason is always printed to stdout, which CloudWatch captures, so
a silent fallback is still diagnosable without a debug redeploy.
"""

from __future__ import annotations

import json
import uuid

import boto3
from botocore.config import Config

from runtime_config_arns import POLICY_MATCHING_ARN

# bedrock-agentcore requires 33-256 character runtimeSessionId values.
_MIN_SESSION_ID_LEN = 33

PEER_AGENT_NAME = "policy-matching"
RESOLVED_VIA = "runtime_config_arns.POLICY_MATCHING_ARN"

# One attempt, bounded wait: a slow or missing peer must not stall Medical
# Necessity's own response to the Orchestrator.
_CLIENT_CONFIG = Config(
    connect_timeout=5,
    read_timeout=30,
    retries={"total_max_attempts": 1, "mode": "standard"},
)


class PeerUnavailableError(Exception):
    """Policy Matching could not be resolved or invoked — caller falls back
    to the generic PEND behavior."""


def _log(message: str) -> None:
    print(f"[peer_client] {message}", flush=True)


def _fail(message: str, cause: BaseException | None = None) -> PeerUnavailableError:
    _log(f"PeerUnavailableError: {message}")
    error = PeerUnavailableError(message)
    if cause is not None:
        error.__cause__ = cause
    return error


def build_clarification_payload(
    *, required_docs_list: list[str], missing_documents: list[str], request_id: str
) -> dict:
    """The complete, scoped payload sent to the peer — no clinical notes,
    notes summary, coverage criteria, or patient demographics."""
    return {
        "clarification_request": True,
        "required_docs_list": list(required_docs_list),
        "missing_documents": list(missing_documents),
        "request_id": request_id,
    }


def _read_streaming_body(value):
    if hasattr(value, "read"):
        value = value.read()
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        value = json.loads(value)
    return value


def request_clarification(
    *, required_docs_list: list[str], missing_documents: list[str], request_id: str
) -> list[str]:
    """Asks Policy Matching which required documents correspond to the
    evidence gap, and returns its confirmed missing-document list."""
    if not POLICY_MATCHING_ARN:
        raise _fail("POLICY_MATCHING_ARN is blank in runtime_config_arns.py")

    payload = build_clarification_payload(
        required_docs_list=required_docs_list,
        missing_documents=missing_documents,
        request_id=request_id,
    )
    rpc_request = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {"message": {"parts": [{"kind": "text", "text": json.dumps(payload)}]}},
    }
    session_id = str(uuid.uuid4())
    assert len(session_id) >= _MIN_SESSION_ID_LEN

    try:
        # The Runtime ARN carries its region (arn:aws:bedrock-agentcore:<region>:...).
        region = POLICY_MATCHING_ARN.split(":")[3] or None
        client = boto3.client("bedrock-agentcore", region_name=region, config=_CLIENT_CONFIG)
        _log(
            f"invoking {PEER_AGENT_NAME} for request_id={request_id!r} "
            f"payload_keys={sorted(payload)}"
        )
        response = client.invoke_agent_runtime(
            agentRuntimeArn=POLICY_MATCHING_ARN,
            runtimeSessionId=session_id,
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(rpc_request).encode("utf-8"),
        )
        rpc_response = _read_streaming_body(response.get("response", response))
    except Exception as exc:  # noqa: BLE001 - every failure means "peer unavailable"
        raise _fail(f"InvokeAgentRuntime failed: {type(exc).__name__}: {exc}", exc) from exc

    if isinstance(rpc_response, dict) and "error" in rpc_response:
        error = rpc_response["error"] or {}
        raise _fail(
            f"{PEER_AGENT_NAME} returned JSON-RPC error {error.get('code')}: {error.get('message')}"
        )

    try:
        output_text = rpc_response["result"]["artifacts"][0]["parts"][0]["text"]
        output = json.loads(output_text)
        confirmed = [str(doc) for doc in output["missing_documents"]]
    except Exception as exc:  # noqa: BLE001 - malformed peer response
        raise _fail(f"unexpected response shape from {PEER_AGENT_NAME}: {exc}", exc) from exc

    if not confirmed:
        raise _fail(f"{PEER_AGENT_NAME} confirmed no missing documents for a known gap")

    _log(f"{PEER_AGENT_NAME} confirmed missing_documents={confirmed}")
    return confirmed
