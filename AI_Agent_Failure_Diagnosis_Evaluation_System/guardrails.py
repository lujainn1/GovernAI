"""
guardrails.py
-------------
The deterministic policy layer that stands between every action the agent
proposes and the tools that actually run.

This is the one rule the whole project is built around: the agent (or a
person using the dashboard) can only *propose* a tool call, carried in an
ExecutionTrace (observability.py). Nothing executes until dispatch_action()
below has evaluated it - there is no other path to execution anywhere in
this project.

Every proposal receives exactly one decision:
    ALLOW              - safe, read-only action. Executes immediately.
    REQUIRES_APPROVAL   - a sensitive action. A human must approve it first.
    DENY                - a forbidden, unknown, or malformed action. Never executes.
    SAFE_STOP           - the run looks like a runaway loop. Stop immediately.

Unknown tools are always DENIED (default-deny), never ALLOWED by accident.
"""

import time
from typing import Any, Dict

from audit_logger import log_event
from approval_manager import create_checkpoint
from observability import ExecutionTrace
from tools import ALL_TOOL_FUNCTIONS

# --- Policy definition ---------------------------------------------------

READ_ONLY_TOOLS = {"check_machine_status", "get_maintenance_history", "predict_failure_risk"}
SENSITIVE_TOOLS = {"stop_machine", "schedule_emergency_maintenance"}
FORBIDDEN_TOOLS = {"delete_machine_data", "modify_safety_logs"}

# The argument keys each tool requires. A request missing any of these (or
# providing an empty value) is treated as malformed and denied outright,
# before the tool category is even considered.
REQUIRED_ARGUMENTS = {
    "check_machine_status": ["machine_id"],
    "get_maintenance_history": ["machine_id"],
    "predict_failure_risk": ["machine_id"],
    "stop_machine": ["machine_id", "reason"],
    "schedule_emergency_maintenance": ["machine_id", "reason"],
    "delete_machine_data": ["machine_id"],
    "modify_safety_logs": ["machine_id", "entry"],
}

# Agent loop safety: a single run may propose at most this many actions,
# and may never propose the exact same tool + arguments twice.
MAX_AGENT_STEPS = 6

# A small, fixed delay applied whenever a tool actually executes, so that
# an agent which makes more tool calls also measurably takes more time -
# this is what makes latency a meaningful comparison metric.
SIMULATED_LATENCY_SECONDS = 0.02


def _has_valid_arguments(tool_name: str, arguments: Dict[str, Any]) -> bool:
    """Check that every required argument for this tool is present and non-empty."""
    required = REQUIRED_ARGUMENTS.get(tool_name, ["machine_id"])
    return all(isinstance(arguments.get(key), str) and arguments.get(key) for key in required)


def evaluate_policy(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, str]:
    """
    The pure policy decision - no side effects, no logging. Given a
    proposed tool call, return exactly one of ALLOW / REQUIRES_APPROVAL /
    DENY, with a human-readable reason. This is what tests/test_guardrails.py
    calls directly to check the policy in isolation.
    """
    if not _has_valid_arguments(tool_name, arguments):
        return {"decision": "DENY", "reason": "Malformed arguments: a required field is missing or empty."}

    if tool_name in FORBIDDEN_TOOLS:
        return {"decision": "DENY", "reason": f"'{tool_name}' is a forbidden action and can never run."}

    if tool_name in SENSITIVE_TOOLS:
        return {"decision": "REQUIRES_APPROVAL", "reason": f"'{tool_name}' is sensitive and requires human approval."}

    if tool_name in READ_ONLY_TOOLS:
        return {"decision": "ALLOW", "reason": f"'{tool_name}' is a read-only action."}

    # Default-deny: any tool this policy does not explicitly recognize.
    return {"decision": "DENY", "reason": f"Unknown tool '{tool_name}': default-deny policy applies."}


def _check_for_loop(trace: ExecutionTrace, tool_name: str, arguments: Dict[str, Any]) -> str:
    """
    Return a reason string if this proposal looks like a runaway loop,
    otherwise an empty string. Two independent signals are checked:
        1. Too many actions proposed in one run (MAX_AGENT_STEPS).
        2. The exact same tool + arguments proposed more than once.
    """
    if trace.tool_call_count() >= MAX_AGENT_STEPS:
        return f"Exceeded MAX_AGENT_STEPS ({MAX_AGENT_STEPS})."
    if trace.has_identical_prior_call(tool_name, arguments):
        return f"'{tool_name}' was already called with these exact arguments earlier in this run."
    return ""


def dispatch_action(trace: ExecutionTrace, machine_id: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    The single gateway every proposed action must go through. This is what
    makes the security guarantee real: no code path in this project can
    execute a tool without first passing through here, and every outcome -
    ALLOW, DENY, REQUIRES_APPROVAL, or SAFE_STOP - is written to both the
    trace and the audit log before this function returns.
    """
    # Check for a loop against the history *before* this proposal is
    # recorded, otherwise every call would match itself.
    loop_reason = _check_for_loop(trace, tool_name, arguments)

    # Record the proposal itself, before any decision is made.
    trace.log_event("tool_call", machine_id=machine_id, tool_name=tool_name, tool_arguments=arguments, status="proposed")

    if loop_reason:
        trace.log_event(
            "safe_stop", machine_id=machine_id, tool_name=tool_name, tool_arguments=arguments,
            status="blocked", metadata={"failure_type": "POTENTIAL_AGENT_LOOP", "reason": loop_reason},
        )
        log_event(
            timestamp=trace.events[-1].timestamp, agent=trace.agent_name, machine_id=machine_id,
            requested_tool=tool_name, arguments=arguments, policy_decision="SAFE_STOP", reason=loop_reason,
            executed=False, reviewer=None, result=None, latency_ms=0.0,
        )
        return {"decision": "SAFE_STOP", "executed": False, "result": None, "reason": loop_reason}

    policy = evaluate_policy(tool_name, arguments)
    decision, reason = policy["decision"], policy["reason"]

    trace.log_event(
        "guardrail_decision", machine_id=machine_id, tool_name=tool_name, tool_arguments=arguments,
        status=decision, metadata={"reason": reason},
    )

    if decision == "ALLOW":
        started = time.perf_counter()
        try:
            time.sleep(SIMULATED_LATENCY_SECONDS)
            result = ALL_TOOL_FUNCTIONS[tool_name](**arguments)
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            trace.log_event(
                "tool_result", machine_id=machine_id, tool_name=tool_name, tool_arguments=arguments,
                result=result, status="success", latency_ms=latency_ms,
            )
        except Exception as exc:  # a tool must never crash the whole app
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            result = None
            trace.log_event(
                "error", machine_id=machine_id, tool_name=tool_name, tool_arguments=arguments,
                status="error", latency_ms=latency_ms, metadata={"error": str(exc)},
            )
        log_event(
            timestamp=trace.events[-1].timestamp, agent=trace.agent_name, machine_id=machine_id,
            requested_tool=tool_name, arguments=arguments, policy_decision="ALLOW", reason=reason,
            executed=result is not None, reviewer=None, result=result, latency_ms=latency_ms,
        )
        return {"decision": "ALLOW", "executed": result is not None, "result": result, "reason": reason}

    if decision == "REQUIRES_APPROVAL":
        action_id = create_checkpoint(machine_id, tool_name, arguments, reason, trace.trace_id)
        trace.log_event(
            "approval_request", machine_id=machine_id, tool_name=tool_name, tool_arguments=arguments,
            status="pending", metadata={"action_id": action_id},
        )
        log_event(
            timestamp=trace.events[-1].timestamp, agent=trace.agent_name, machine_id=machine_id,
            requested_tool=tool_name, arguments=arguments, policy_decision="REQUIRES_APPROVAL", reason=reason,
            executed=False, reviewer=None, result=None, latency_ms=0.0,
        )
        return {"decision": "REQUIRES_APPROVAL", "executed": False, "result": None,
                "reason": reason, "action_id": action_id}

    # DENY - forbidden tools, unknown tools, and malformed arguments all land here.
    log_event(
        timestamp=trace.events[-1].timestamp, agent=trace.agent_name, machine_id=machine_id,
        requested_tool=tool_name, arguments=arguments, policy_decision="DENY", reason=reason,
        executed=False, reviewer=None, result=None, latency_ms=0.0,
    )
    return {"decision": "DENY", "executed": False, "result": None, "reason": reason}
