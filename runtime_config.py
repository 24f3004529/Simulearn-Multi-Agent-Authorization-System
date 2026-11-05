"""
Real AgentCore Runtime ARNs, keyed by capability. Not trusted as the actual
capability claim — runtime_client.py uses these only to know where to call
GetAgentCard; a wrong ARN-to-key pairing fails loudly at startup instead of
silently misrouting a request. Leave a value as "" until that agent is
deployed.
"""

from __future__ import annotations

AGENT_RUNTIME_ARNS: dict[str, str] = {
    "clinical_extraction": "",
    "policy_matching": "",
    "medical_necessity": "",
    "decision_synthesis": "",
}
