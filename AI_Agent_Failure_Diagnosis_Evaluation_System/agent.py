"""
agent.py
--------
The Predictive Maintenance agent's workflow, in two versions:

    "improved" - the correct, disciplined agent.
    "baseline" - an intentionally flawed agent, used for comparison in Part 4.

Both versions share the same tools (tools.py) and are driven by the same
free-text query. Only the *behavior* differs, so the two can be compared
fairly on the exact same evaluation dataset (evaluator.py).

Every run gets its own ExecutionTrace (observability.py) with a unique
trace_id. This file never calls a tool directly and never decides policy -
it only logs its own "decision" (which tool it is about to propose, and
why, in one observable sentence - never private chain-of-thought) and then
proposes that action through guardrails.dispatch_action(), which is the
only code path in this project allowed to execute a tool.

Normal (improved) workflow:
    User Query -> Identify Machine -> Check Status -> Check Maintenance
                -> Predict Failure Risk -> Final Diagnosis -> Recommendation

The baseline agent demonstrates three realistic, general bugs (not tied to
any specific test case):
    1. It always calls every tool, in the wrong order, with one duplicate
       call - regardless of what was actually asked. The duplicate call is
       now caught by the guardrail's loop detection (see guardrails.py),
       which force-stops the run instead of letting it repeat.
    2. If it cannot find a machine ID in the query, it silently guesses one
       instead of asking for clarification. This shows up as
       "Missing Information".
    3. It always appends one unverified sentence to its final answer, which
       is never grounded in any tool result. This shows up as
       "Unsupported Claim".
"""

import re
import time
from typing import Any, Dict, List, Optional

from tools import MACHINES, is_known_machine, get_risk_level
from guardrails import dispatch_action
from observability import ExecutionTrace

# The order tools are expected to run in for a correct diagnosis.
EXPECTED_TOOL_ORDER = ["check_machine_status", "get_maintenance_history", "predict_failure_risk"]

# The baseline agent's fixed (buggy) tool-call sequence: wrong first tool,
# plus one duplicate call, regardless of what the user actually asked.
BASELINE_TOOL_SEQUENCE = [
    "predict_failure_risk",
    "check_machine_status",
    "check_machine_status",
    "get_maintenance_history",
]

# Text the baseline agent substitutes whenever a tool call comes back empty
# (for example, an unknown machine ID) instead of reporting the gap honestly.
BASELINE_FILLER = "Based on typical patterns, this machine is likely operating within normal parameters."

# A sentence the baseline agent always appends, regardless of the real data.
# It is never grounded in any tool result - this is the "Unsupported Claim".
BASELINE_UNSUPPORTED_CLAIM = "This machine is guaranteed to operate safely until the next scheduled service."

# Recognizes a machine-like token such as "CNC-01" or "PRESS-99" inside free text.
_MACHINE_TOKEN_PATTERN = re.compile(r"\b[A-Z]+-\d+\b")

# Keywords used by the improved agent to decide which tools a question needs.
_STATUS_KEYWORDS = ["status", "condition", "operating"]
_HISTORY_KEYWORDS = ["maintenance", "history", "serviced", "repaired"]
_RISK_KEYWORDS = ["risk", "fail"]
_FULL_KEYWORDS = ["full diagnosis", "complete diagnosis"]


def identify_machine_id(query: str) -> Optional[str]:
    """
    'Identify Machine' step: look for a machine-like token (letters, a
    hyphen, then digits - e.g. "CNC-01") anywhere in the user's text.

    Returns the token in upper case if one is found (even if it turns out
    not to be a known machine), or None if no such token is present at all.
    """
    match = _MACHINE_TOKEN_PATTERN.search(query.upper())
    return match.group(0) if match else None


def determine_needed_tools(query: str) -> Dict[str, bool]:
    """
    Decide which of the three read-only tools are actually relevant to a
    question, based on simple keyword matching. Used only by the improved
    agent - the baseline agent ignores this and always calls every tool.
    """
    query_lower = query.lower()

    if any(keyword in query_lower for keyword in _FULL_KEYWORDS):
        return {"status": True, "history": True, "risk": True}

    needs = {
        "status": any(keyword in query_lower for keyword in _STATUS_KEYWORDS),
        "history": any(keyword in query_lower for keyword in _HISTORY_KEYWORDS),
        "risk": any(keyword in query_lower for keyword in _RISK_KEYWORDS),
    }
    # Safety default: if a valid machine was named but no keyword matched
    # anything specific, at least check its current status.
    if not any(needs.values()):
        needs["status"] = True
    return needs


def build_recommendation(risk_level: Optional[str]) -> str:
    """Turn a risk level word into a plain-language recommendation."""
    if risk_level == "High":
        return "Schedule immediate inspection and consider stopping the machine until it is checked."
    if risk_level == "Medium":
        return "Increase monitoring frequency and plan a maintenance check soon."
    if risk_level == "Low":
        return "No action needed. Continue routine monitoring."
    return "Run a risk prediction for a specific recommendation."


def _new_result(query: str, agent_version: str, trace: ExecutionTrace) -> Dict[str, Any]:
    """Create an empty result dictionary with every field the rest of the
    project (evaluator.py, failure_detector.py, app.py) expects to find."""
    return {
        "agent_version": agent_version,
        "query": query,
        "trace_id": trace.trace_id,
        "machine_id": None,
        "status_result": None,
        "history_result": None,
        "risk_result": None,
        "diagnosis": "",
        "recommendation": "",
        "tool_calls": [],
        "evidence": [],
        "agent_status": "completed",
        "latency_ms": 0.0,
        "trace": [],  # kept for backward compatibility with app.py's simple trace view
    }


def _log(result: Dict[str, Any], step: str, tool: Optional[str], tool_input: Optional[str], output: Any) -> None:
    """Append one entry to the simple, human-readable trace list on the result dict."""
    from datetime import datetime

    result["trace"].append(
        {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "step": step,
            "tool": tool,
            "tool_input": tool_input,
            "output": output,
        }
    )


def _propose_read_only(
    result: Dict[str, Any], trace: ExecutionTrace, machine_id: str, tool_name: str, step_label: str
) -> Any:
    """
    Log the agent's decision, then propose one read-only tool call through
    the guardrail layer. Records the outcome in both the result's
    tool_calls/evidence lists and the simple trace, and returns the tool's
    output (or None if the guardrail blocked it, e.g. SAFE_STOP).
    """
    trace.log_event("decision", machine_id=machine_id, tool_name=tool_name, metadata={"reason": f"Query needs {tool_name}."})
    outcome = dispatch_action(trace, machine_id, tool_name, {"machine_id": machine_id})
    output = outcome["result"]
    if outcome["decision"] == "ALLOW":
        result["tool_calls"].append(tool_name)
        result["evidence"].append(f"{tool_name}:{machine_id}")
    _log(result, step_label, tool_name, machine_id, output)
    return outcome, output


def run_diagnosis(query: str, agent_version: str = "improved") -> Dict[str, Any]:
    """
    Run the Predictive Maintenance workflow for one free-text query, using
    either the "improved" (correct) or "baseline" (intentionally flawed)
    agent. Returns a dictionary describing everything that happened:
    machine_id, individual tool results, the final diagnosis and
    recommendation, the full execution trace, evidence, agent status, and
    latency in milliseconds.
    """
    start_time = time.perf_counter()
    trace = ExecutionTrace(agent_name=agent_version, query=query)
    result = _new_result(query, agent_version, trace)

    if agent_version == "baseline":
        _run_baseline(query, result, trace)
    else:
        _run_improved(query, result, trace)

    result["latency_ms"] = round((time.perf_counter() - start_time) * 1000, 2)
    trace.finish(result["agent_status"], {"diagnosis": result["diagnosis"], "recommendation": result["recommendation"]})
    return result


def _run_improved(query: str, result: Dict[str, Any], trace: ExecutionTrace) -> None:
    """The correct, disciplined agent."""
    machine_id = identify_machine_id(query)
    result["machine_id"] = machine_id

    # No machine mentioned at all: ask for clarification instead of guessing.
    if machine_id is None:
        _log(result, "Identify Machine", None, None, "No machine ID found in the question.")
        result["agent_status"] = "needs_clarification"
        result["diagnosis"] = (
            "I could not identify a machine ID in your question. "
            f"Please specify one of: {', '.join(MACHINES)}."
        )
        result["recommendation"] = "Please specify a machine ID and try again."
        return

    # A machine-like token was found, but it is not one we have data for.
    if not is_known_machine(machine_id):
        _log(result, "Identify Machine", None, machine_id, "Machine ID not recognized.")
        result["agent_status"] = "invalid_machine_id"
        result["diagnosis"] = (
            f"'{machine_id}' is not recognized as a valid machine ID. "
            f"Known machines are: {', '.join(MACHINES)}."
        )
        result["recommendation"] = "Please check the machine ID and try again."
        return

    _log(result, "Identify Machine", None, machine_id, f"Machine identified: {machine_id}")

    # Decide which tools this specific question actually needs, then
    # propose each one through the guardrail layer.
    needs = determine_needed_tools(query)
    diagnosis_parts = [f"{machine_id} -"]

    tool_plan = [
        ("status", "check_machine_status", "status_result", "Check Machine Status", "Status"),
        ("history", "get_maintenance_history", "history_result", "Check Maintenance History", "Maintenance"),
        ("risk", "predict_failure_risk", "risk_result", "Predict Failure Risk", "Risk assessment"),
    ]
    for needs_key, tool_name, field_name, step_label, diagnosis_label in tool_plan:
        if not needs[needs_key]:
            continue
        outcome, output = _propose_read_only(result, trace, machine_id, tool_name, step_label)
        if outcome["decision"] == "SAFE_STOP":
            result["agent_status"] = "safe_stop"
            result["diagnosis"] = f"Execution stopped for {machine_id}: a potential agent loop was detected."
            result["recommendation"] = "Review the execution trace before retrying this request."
            return
        result[field_name] = output
        diagnosis_parts.append(f"{diagnosis_label}: {output}")

    result["diagnosis"] = " ".join(diagnosis_parts)
    _log(result, "Generate Final Diagnosis", None, None, result["diagnosis"])

    risk_level = get_risk_level(machine_id) if needs["risk"] else None
    result["recommendation"] = build_recommendation(risk_level)
    _log(result, "Provide Recommendation", None, None, result["recommendation"])


def _run_baseline(query: str, result: Dict[str, Any], trace: ExecutionTrace) -> None:
    """
    The intentionally flawed agent used for comparison. See the module
    docstring above for the three bugs this function demonstrates. Its
    built-in duplicate tool call is now caught by the guardrail's loop
    detection, which stops the run instead of letting it repeat.
    """
    machine_id = identify_machine_id(query)

    # Bug: instead of asking for clarification, silently guess a default machine.
    if machine_id is None:
        machine_id = "CNC-01"
        _log(result, "Identify Machine", None, None, f"No machine ID found - defaulting to {machine_id}.")
    else:
        _log(result, "Identify Machine", None, machine_id, f"Machine identified: {machine_id}")

    result["machine_id"] = machine_id

    # Bug: always call every tool, in the wrong order, with one duplicate -
    # regardless of whether the machine ID is valid or what was actually asked.
    latest_output: Dict[str, Any] = {}
    for tool_name in BASELINE_TOOL_SEQUENCE:
        trace.log_event(
            "decision", machine_id=machine_id, tool_name=tool_name,
            metadata={"reason": "Baseline policy: always call every tool."},
        )
        outcome = dispatch_action(trace, machine_id, tool_name, {"machine_id": machine_id})

        if outcome["decision"] == "SAFE_STOP":
            result["agent_status"] = "safe_stop"
            result["diagnosis"] = f"Execution stopped for {machine_id}: a potential agent loop was detected."
            result["recommendation"] = "Review the execution trace before retrying this request."
            _log(result, "Safe Stop", tool_name, machine_id, outcome["reason"])
            return

        raw_output = outcome["result"]
        if raw_output is None:
            # Bug: paper over missing data with a generic, unverified filler
            # sentence instead of reporting that nothing was found.
            output = BASELINE_FILLER
        else:
            output = raw_output
            result["evidence"].append(f"{tool_name}:{machine_id}")
        result["tool_calls"].append(tool_name)
        latest_output[tool_name] = output
        _log(result, f"Call {tool_name}", tool_name, machine_id, output)

    result["status_result"] = latest_output.get("check_machine_status")
    result["history_result"] = latest_output.get("get_maintenance_history")
    result["risk_result"] = latest_output.get("predict_failure_risk")

    # Bug: always add one more sentence that no tool ever produced.
    result["diagnosis"] = (
        f"{machine_id} - Status: {result['status_result']} "
        f"Maintenance: {result['history_result']} "
        f"Risk assessment: {result['risk_result']} "
        f"{BASELINE_UNSUPPORTED_CLAIM}"
    )
    _log(result, "Generate Final Diagnosis", None, None, result["diagnosis"])

    result["recommendation"] = build_recommendation(get_risk_level(machine_id))
    _log(result, "Provide Recommendation", None, None, result["recommendation"])

    result["agent_status"] = "completed"


def run_max_steps_demo(num_steps: int = 8) -> List[Dict[str, Any]]:
    """
    Demonstrates the MAX_AGENT_STEPS half of loop monitoring in isolation:
    propose a *different* (tool, machine) combination every time, so the
    "identical repeated call" rule never fires, purely to show the step
    budget itself forcing a SAFE_STOP once it is used up.

    There are only 3 machines and 3 read-only tools, so up to 9 genuinely
    distinct combinations are available - more than enough to go past
    MAX_AGENT_STEPS (6) without ever repeating an exact prior call.
    """
    from tools import MACHINE_IDS

    read_only_tools = ["check_machine_status", "get_maintenance_history", "predict_failure_risk"]
    combos = [(tool, machine) for tool in read_only_tools for machine in MACHINE_IDS]

    trace = ExecutionTrace(agent_name="loop_demo_agent", query="Propose several distinct safe actions in a row.")
    demo_trace = []
    for i in range(min(num_steps, len(combos))):
        tool_name, machine_id = combos[i]
        outcome = dispatch_action(trace, machine_id, tool_name, {"machine_id": machine_id})
        demo_trace.append({"step": i + 1, "tool": tool_name, "machine_id": machine_id, "decision": outcome["decision"]})
        if outcome["decision"] == "SAFE_STOP":
            break
    trace.finish("safe_stop" if demo_trace[-1]["decision"] == "SAFE_STOP" else "success", demo_trace)
    return demo_trace


def run_repeated_call_demo(machine_id: str = "CNC-01") -> List[Dict[str, Any]]:
    """
    Demonstrates the "identical repeated call" half of loop monitoring:
    propose the exact same tool with the exact same arguments twice in a
    row. The second attempt is caught immediately, long before
    MAX_AGENT_STEPS would ever be reached.
    """
    trace = ExecutionTrace(agent_name="loop_demo_agent", query=f"Repeatedly check {machine_id} status.")
    demo_trace = []
    for i in range(3):
        outcome = dispatch_action(trace, machine_id, "check_machine_status", {"machine_id": machine_id})
        demo_trace.append({"step": i + 1, "machine_id": machine_id, "decision": outcome["decision"]})
        if outcome["decision"] == "SAFE_STOP":
            break
    trace.finish("safe_stop" if demo_trace[-1]["decision"] == "SAFE_STOP" else "success", demo_trace)
    return demo_trace
