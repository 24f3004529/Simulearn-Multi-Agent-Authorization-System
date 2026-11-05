"""
DIY Part 2 placeholder — container-local A2A client for Medical Necessity's
peer call to Policy Matching. Fill in request_clarification() below.
"""

from __future__ import annotations

import json
import uuid

import boto3

from runtime_config_arns import POLICY_MATCHING_ARN

# bedrock-agentcore requires 33-256 character runtimeSessionId values.
_MIN_SESSION_ID_LEN = 33


class PeerUnavailableError(Exception):
    """Policy Matching could not be resolved or invoked — caller falls back
    to the generic PEND behavior."""


def request_clarification(
    *, required_docs_list: list[str], missing_documents: list[str], request_id: str
) -> list[str]:
    # TODO(DIY Part 2): raise PeerUnavailableError if POLICY_MATCHING_ARN is
    # still blank, then call InvokeAgentRuntime with a scoped payload
    # (required_docs_list, missing_documents, request_id only — no clinical
    # or demographic fields) and return the confirmed missing-document list
    # from Policy Matching's response. Catch any boto3 error and re-raise it
    # as PeerUnavailableError so the caller can fall back to generic PEND.
    raise NotImplementedError
