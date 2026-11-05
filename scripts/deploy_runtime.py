#!/usr/bin/env python3
"""
Redeploys an EXISTING AgentCore Runtime with a freshly built zip
(update-agent-runtime, never create-agent-runtime — the Runtime already
exists). Every setting already on the Runtime (role, network, A2A protocol
configuration, env vars, ...) is carried over unchanged; only the code
artifact's S3 location changes. Polls until READY and prints failureReason
on failure.

    python3 scripts/deploy_runtime.py policy_matching   --bucket <AgentRuntimeCodeBucket name>
    python3 scripts/deploy_runtime.py medical_necessity --bucket <AgentRuntimeCodeBucket name>

The bucket may also come from $AGENT_RUNTIME_CODE_BUCKET. Build the zip first
with scripts/package_runtime.sh <capability>_runtime.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime_config import AGENT_RUNTIME_ARNS  # noqa: E402

TERMINAL_FAILURES = {"CREATE_FAILED", "UPDATE_FAILED", "DELETING", "DELETE_FAILED"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("capability", choices=sorted(AGENT_RUNTIME_ARNS))
    parser.add_argument("--bucket", default=os.environ.get("AGENT_RUNTIME_CODE_BUCKET"))
    parser.add_argument("--key", help="S3 key (default: <capability>_runtime.zip)")
    parser.add_argument("--zip", help="zip path (default: agents/<capability>_runtime.zip)")
    parser.add_argument("--timeout", type=int, default=600, help="seconds to wait for READY")
    args = parser.parse_args()

    if not args.bucket:
        parser.error("--bucket (or $AGENT_RUNTIME_CODE_BUCKET) is required")
    runtime_arn = AGENT_RUNTIME_ARNS[args.capability]
    if not runtime_arn:
        parser.error(f"runtime_config.py has no ARN for {args.capability}")
    zip_path = Path(args.zip or ROOT / "agents" / f"{args.capability}_runtime.zip")
    if not zip_path.is_file():
        parser.error(f"{zip_path} not found — run scripts/package_runtime.sh {args.capability}_runtime")
    key = args.key or zip_path.name
    region = runtime_arn.split(":")[3]
    runtime_id = runtime_arn.split("runtime/", 1)[1]

    control = boto3.client("bedrock-agentcore-control", region_name=region)
    current = control.get_agent_runtime(agentRuntimeId=runtime_id)
    code_config = current["agentRuntimeArtifact"].get("codeConfiguration")
    if not code_config:
        print("This Runtime is not a direct code deployment (no codeConfiguration).")
        return 1

    print(f"uploading {zip_path} -> s3://{args.bucket}/{key}")
    s3 = boto3.client("s3", region_name=region)
    s3.upload_file(str(zip_path), args.bucket, key)
    version_id = s3.head_object(Bucket=args.bucket, Key=key).get("VersionId")

    s3_location = {"bucket": args.bucket, "prefix": key}
    if version_id:
        s3_location["versionId"] = version_id

    # Carry over every updatable setting the Runtime already has.
    update_members = control.meta.service_model.operation_model("UpdateAgentRuntime").input_shape.members
    carried = {
        name: current[name]
        for name in update_members
        if name not in {"agentRuntimeId", "agentRuntimeArtifact", "clientToken"}
        and current.get(name) not in (None, {}, [])
    }
    request = {
        **carried,
        "agentRuntimeId": runtime_id,
        "agentRuntimeArtifact": {
            "codeConfiguration": {
                "code": {"s3": s3_location},
                "runtime": code_config["runtime"],
                "entryPoint": code_config["entryPoint"],
            }
        },
    }
    print(f"update-agent-runtime {runtime_id} (carrying over: {sorted(carried)})")
    control.update_agent_runtime(**request)

    deadline = time.time() + args.timeout
    while True:
        status = control.get_agent_runtime(agentRuntimeId=runtime_id)
        state = status["status"]
        print(f"  status={state} version={status.get('agentRuntimeVersion')}")
        if state == "READY":
            print(f"{args.capability} is READY: {runtime_arn}")
            return 0
        if state in TERMINAL_FAILURES:
            print(f"FAILED: {status.get('failureReason')}")
            return 1
        if time.time() > deadline:
            print("timed out waiting for READY")
            return 1
        time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
