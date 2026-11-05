"""
DIY Part 2 — container-local reference to Policy Matching's Runtime ARN, used
only by peer_client.py's peer call. AgentCard has no ARN field, so this cannot
come from AgentCardRegistryTable; it is a plain constant.

Keep in sync with ../../runtime_config.py's AGENT_RUNTIME_ARNS["policy_matching"]:
regenerate with `python3 scripts/gen_runtime_config_arns.py` (also run by
scripts/package_runtime.sh before zipping).
"""

POLICY_MATCHING_ARN = "arn:aws:bedrock-agentcore:us-east-1:301463937497:runtime/policy_matching-uIfcHcFdPa"
