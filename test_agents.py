"""
Quick per-agent verification: calls verify_single_agent() for every
capability that has a real ARN recorded in runtime_config.py, skipping any
that are still blank. Run after deploying each Runtime, one at a time or
all together.

Usage: python3 test_agents.py
"""

from __future__ import annotations

from runtime_client import verify_single_agent
from runtime_config import AGENT_RUNTIME_ARNS


def main() -> None:
    for capability, runtime_arn in AGENT_RUNTIME_ARNS.items():
        if not runtime_arn:
            print(f"{capability}: skipped (no ARN recorded in runtime_config.py)")
            continue

        print(f"{capability}: calling {runtime_arn} ...")
        try:
            result = verify_single_agent(capability, runtime_arn)
            print(f"{capability}: OK -> {result}")
        except Exception as exc:  # noqa: BLE001 - surface the real failure, don't mask it
            print(f"{capability}: FAILED -> {exc}")
        print()


if __name__ == "__main__":
    main()
