"""
Real AgentCore Runtime ARNs, keyed by capability. Not trusted as the actual
capability claim — runtime_client.py uses these only to know where to call
GetAgentCard; a wrong ARN-to-key pairing fails loudly at startup instead of
silently misrouting a request. Leave a value as "" until that agent is
deployed.
"""

from __future__ import annotations

AGENT_RUNTIME_ARNS: dict[str, str] = {
    "clinical_extraction": "arn:aws:bedrock-agentcore:us-east-1:301463937497:runtime/clinical_extraction-hxYLg5H9HI",
    "policy_matching": "arn:aws:bedrock-agentcore:us-east-1:301463937497:runtime/policy_matching-uIfcHcFdPa",
    "medical_necessity": "arn:aws:bedrock-agentcore:us-east-1:301463937497:runtime/medical_necessity-Y9FgA03xa7",
    "decision_synthesis": "arn:aws:bedrock-agentcore:us-east-1:301463937497:runtime/decision_synthesis-y3BWJQDw5B",
}
