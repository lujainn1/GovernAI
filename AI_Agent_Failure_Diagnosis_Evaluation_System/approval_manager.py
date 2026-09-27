"""
approval_manager.py
--------------------
Manages the human-in-the-loop approval boundary for sensitive actions.

When guardrails.py decides a proposed action REQUIRES_APPROVAL, it calls
create_checkpoint() here instead of running the tool. The action then
waits - exactly as PENDING_APPROVAL - until a human reviewer calls
approve() or reject(). Nothing in this module ever executes a tool on its
own initiative.

Every checkpoint remembers which ExecutionTrace (observability.py)
proposed it, so that when a reviewer finally makes a decision - possibly
much later, from a different page of the dashboard - the "approval_result"
(and, if approved, "tool_result") events are appended to that same
original trace, keeping the whole run inspectable end to end.

Status values a checkpoint can hold:
    PENDING_APPROVAL - waiting for a human decision. Nothing has run yet.
    EXECUTED         - a reviewer approved it, and the tool ran exactly once.
    REJECTED         - a reviewer rejected it. The tool never ran.
"""

import time
from datetime import datetime
from itertools import count
from typing import Any, Dict, List, Optional

from tools import ALL_TOOL_FUNCTIONS
from audit_logger import log_event
import observability

PENDING_APPROVAL = "PENDING_APPROVAL"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
EXECUTED = "EXECUTED"

# Matches guardrails.SIMULATED_LATENCY_SECONDS, so an approved action takes
# roughly the same simulated time to run as any other executed tool call.
SIMULATED_LATENCY_SECONDS = 0.02

# The in-memory "database" of checkpoints, keyed by action_id.
_CHECKPOINTS: Dict[str, Dict[str, Any]] = {}
_action_id_counter = count(1)


def create_checkpoint(
    machine_id: str, tool_name: str, arguments: Dict[str, Any], reason: str, trace_id: Optional[str] = None
) -> str:
    """Save a new pending action and return its action_id."""
    action_id = f"ACT-{next(_action_id_counter):04d}"
    _CHECKPOINTS[action_id] = {
        "action_id": action_id,
        "machine_id": machine_id,
        "tool_name": tool_name,
        "arguments": arguments,
        "reason": reason,
        "trace_id": trace_id,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "status": PENDING_APPROVAL,
        "reviewer": None,
        "approval_timestamp": None,
        "result": None,
    }
    return action_id


def list_pending() -> List[Dict[str, Any]]:
    """All checkpoints still waiting for a human decision."""
    return [checkpoint for checkpoint in _CHECKPOINTS.values() if checkpoint["status"] == PENDING_APPROVAL]


def list_all() -> List[Dict[str, Any]]:
    """Every checkpoint, regardless of status - used by the dashboard."""
    return list(_CHECKPOINTS.values())


def get_checkpoint(action_id: str) -> Optional[Dict[str, Any]]:
    """Look up one checkpoint by its action_id."""
    return _CHECKPOINTS.get(action_id)


def approve(action_id: str, reviewer: str) -> Dict[str, Any]:
    """
    A human reviewer approves a pending action: the underlying tool runs
    exactly once. Calling this again for the same action_id (a repeated
    execution attempt) is detected and blocked instead of running twice.
    """
    checkpoint = _CHECKPOINTS.get(action_id)
    if checkpoint is None:
        return {"status": "error", "reason": f"No such action: {action_id}"}

    if checkpoint["status"] != PENDING_APPROVAL:
        # Already resolved earlier - refuse to execute a second time.
        return {"status": "blocked", "reason": f"Action {action_id} is already '{checkpoint['status']}'."}

    started = time.perf_counter()
    time.sleep(SIMULATED_LATENCY_SECONDS)
    result = ALL_TOOL_FUNCTIONS[checkpoint["tool_name"]](**checkpoint["arguments"])
    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    checkpoint["status"] = EXECUTED
    checkpoint["reviewer"] = reviewer
    checkpoint["approval_timestamp"] = datetime.now().strftime("%H:%M:%S")
    checkpoint["result"] = result

    # Append the resolution to the original run's trace, if it is still around.
    trace = observability.get_trace(checkpoint["trace_id"]) if checkpoint["trace_id"] else None
    if trace is not None:
        trace.log_event(
            "approval_result", machine_id=checkpoint["machine_id"], tool_name=checkpoint["tool_name"],
            tool_arguments=checkpoint["arguments"], status="approved", metadata={"reviewer": reviewer},
        )
        trace.log_event(
            "tool_result", machine_id=checkpoint["machine_id"], tool_name=checkpoint["tool_name"],
            tool_arguments=checkpoint["arguments"], result=result, status="success", latency_ms=latency_ms,
        )
        observability.save_all()

    log_event(
        timestamp=checkpoint["approval_timestamp"], agent="human_reviewer", machine_id=checkpoint["machine_id"],
        requested_tool=checkpoint["tool_name"], arguments=checkpoint["arguments"], policy_decision="APPROVED",
        reason=f"Approved by {reviewer}.", executed=True, reviewer=reviewer, result=result, latency_ms=latency_ms,
    )
    return {"status": "executed", "result": result}


def reject(action_id: str, reviewer: str) -> Dict[str, Any]:
    """A human reviewer rejects a pending action. The tool never runs."""
    checkpoint = _CHECKPOINTS.get(action_id)
    if checkpoint is None:
        return {"status": "error", "reason": f"No such action: {action_id}"}

    if checkpoint["status"] != PENDING_APPROVAL:
        return {"status": "blocked", "reason": f"Action {action_id} is already '{checkpoint['status']}'."}

    checkpoint["status"] = REJECTED
    checkpoint["reviewer"] = reviewer
    checkpoint["approval_timestamp"] = datetime.now().strftime("%H:%M:%S")

    trace = observability.get_trace(checkpoint["trace_id"]) if checkpoint["trace_id"] else None
    if trace is not None:
        trace.log_event(
            "approval_result", machine_id=checkpoint["machine_id"], tool_name=checkpoint["tool_name"],
            tool_arguments=checkpoint["arguments"], status="rejected", metadata={"reviewer": reviewer},
        )
        observability.save_all()

    log_event(
        timestamp=checkpoint["approval_timestamp"], agent="human_reviewer", machine_id=checkpoint["machine_id"],
        requested_tool=checkpoint["tool_name"], arguments=checkpoint["arguments"], policy_decision="REJECTED",
        reason=f"Rejected by {reviewer}.", executed=False, reviewer=reviewer, result=None, latency_ms=0.0,
    )
    return {"status": "rejected"}


def reset() -> None:
    """Clear all checkpoints. Used by tests so each test starts clean."""
    _CHECKPOINTS.clear()
