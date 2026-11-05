"""
DynamoDB wiring for the DIY Part 1 state backends (AgentCardRegistryTable and
CheckpointTable). Table names and region can be overridden with environment
variables to match whatever names the CDK stack actually provisioned:

    AGENT_CARD_REGISTRY_TABLE   (default: AgentCardRegistryTable)
    CHECKPOINT_TABLE            (default: CheckpointTable)
    AWS_REGION / AWS_DEFAULT_REGION (default: the AWS config's region, else us-east-1)
    PA_STATE_BACKEND            "dynamodb" (default) or "memory" — "memory"
                                keeps the original in-process dicts, for
                                offline local development only.

Table handles are created lazily, so importing this module (or registry.py /
orchestrator.py) never makes a network call.
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_REGION = "us-east-1"


def agent_card_registry_table_name() -> str:
    return os.environ.get("AGENT_CARD_REGISTRY_TABLE", "AgentCardRegistryTable")


def checkpoint_table_name() -> str:
    return os.environ.get("CHECKPOINT_TABLE", "CheckpointTable")


def use_dynamodb() -> bool:
    return os.environ.get("PA_STATE_BACKEND", "dynamodb").strip().lower() != "memory"


def _region() -> str:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if region:
        return region
    import boto3

    return boto3.session.Session().region_name or DEFAULT_REGION


def get_table(table_name: str) -> Any:
    """Returns a boto3 DynamoDB Table resource for `table_name`."""
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ImportError(
            "The DynamoDB-backed registry/checkpoint store requires boto3. "
            "Install it (pip install boto3), or set PA_STATE_BACKEND=memory "
            "for offline local development."
        ) from exc
    return boto3.resource("dynamodb", region_name=_region()).Table(table_name)


class StateBackendError(Exception):
    """A DynamoDB call for the registry/checkpoint store failed (permissions,
    missing table, throttling, ...). Deliberately NOT an AgentNotFoundError,
    so an AWS-side failure never masquerades as "agent not registered"."""
