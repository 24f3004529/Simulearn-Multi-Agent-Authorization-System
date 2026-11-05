#!/usr/bin/env python3
"""
Regenerates agents/medical_necessity_runtime/runtime_config_arns.py from
runtime_config.py's AGENT_RUNTIME_ARNS["policy_matching"], so the
container-local peer ARN can't drift from the one the Orchestrator uses.
Usage: python3 scripts/gen_runtime_config_arns.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime_config import AGENT_RUNTIME_ARNS  # noqa: E402

TARGET = ROOT / "agents" / "medical_necessity_runtime" / "runtime_config_arns.py"

TEMPLATE = '''"""
DIY Part 2 — container-local reference to Policy Matching's Runtime ARN, used
only by peer_client.py's peer call. AgentCard has no ARN field, so this cannot
come from AgentCardRegistryTable; it is a plain constant.

Keep in sync with ../../runtime_config.py's AGENT_RUNTIME_ARNS["policy_matching"]:
regenerate with `python3 scripts/gen_runtime_config_arns.py` (also run by
scripts/package_runtime.sh before zipping).
"""

POLICY_MATCHING_ARN = "{arn}"
'''


def main() -> int:
    arn = AGENT_RUNTIME_ARNS.get("policy_matching", "")
    if not arn:
        print("runtime_config.py has no policy_matching ARN; deploy Policy Matching first.")
        return 1
    content = TEMPLATE.format(arn=arn)
    if TARGET.exists() and TARGET.read_text() == content:
        print(f"{TARGET.relative_to(ROOT)} already up to date ({arn})")
    else:
        TARGET.write_text(content)
        print(f"wrote {TARGET.relative_to(ROOT)} (POLICY_MATCHING_ARN={arn})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
