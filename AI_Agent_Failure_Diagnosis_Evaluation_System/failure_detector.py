"""
failure_detector.py
--------------------
Inspects a completed agent result (as returned by agent.run_diagnosis) and
reports which common AI-agent failure patterns it shows, purely from
observable behavior: tool calls, tool results, evidence, agent status, and
the final response. This module never looks at private model reasoning -
there is none here, only logged, inspectable steps.

Detected patterns:
    - Wrong Tool Selection
    - Repeated Tool Loop
    - Premature Answer
    - Missing Information
    - Invalid Machine ID
    - Unsupported Claim
"""

from typing import Any, Dict, List

from tools import is_known_machine

# The order tools are expected to run in for a correct diagnosis.
EXPECTED_TOOL_ORDER = ["check_machine_status", "get_maintenance_history", "predict_failure_risk"]

# Phrases that indicate a fabricated claim not grounded in any tool result.
# (These are exactly the phrases the baseline agent in agent.py injects.)
UNSUPPORTED_CLAIM_MARKERS = [
    "guaranteed to operate safely",
    "typical patterns",
]


def detect_failures(result: Dict[str, Any]) -> List[str]:
    """Return the list of failure pattern names detected in one agent result."""
    failures: List[str] = []

    tool_calls: List[str] = result.get("tool_calls", [])
    machine_id = result.get("machine_id")
    agent_status = result.get("agent_status")
    query = (result.get("query") or "").upper()
    diagnosis = (result.get("diagnosis") or "").lower()

    # Invalid Machine ID: the agent used a machine ID that isn't a known
    # machine, but still produced a completed diagnosis instead of stopping.
    if machine_id and agent_status == "completed" and not is_known_machine(machine_id):
        failures.append("Invalid Machine ID")

    # Missing Information: the agent proceeded with a machine ID that was
    # never actually present in the user's own query - i.e. it guessed one
    # instead of asking for clarification.
    if machine_id and machine_id not in query and agent_status == "completed":
        failures.append("Missing Information")

    # Wrong Tool Selection: the tools that were called did not run in the
    # expected order (status, then history, then risk).
    tools_called_in_order = [tool for tool in tool_calls if tool in EXPECTED_TOOL_ORDER]
    first_occurrence_order: List[str] = []
    for tool in tools_called_in_order:
        if tool not in first_occurrence_order:
            first_occurrence_order.append(tool)
    canonical_order = [tool for tool in EXPECTED_TOOL_ORDER if tool in first_occurrence_order]
    if first_occurrence_order != canonical_order:
        failures.append("Wrong Tool Selection")

    # Repeated Tool Loop: the same tool was called more than once.
    if len(tool_calls) != len(set(tool_calls)):
        failures.append("Repeated Tool Loop")

    # Premature Answer: a completed diagnosis was produced for a known
    # machine without calling any tool first.
    if agent_status == "completed" and machine_id and not tool_calls:
        failures.append("Premature Answer")

    # Unsupported Claim: the final diagnosis contains a phrase that is not
    # grounded in any tool result.
    if any(marker in diagnosis for marker in UNSUPPORTED_CLAIM_MARKERS):
        failures.append("Unsupported Claim")

    return failures
