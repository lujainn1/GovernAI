"""
tests/test_guardrails.py
-------------------------
Deterministic tests for the guardrail policy layer, the human-in-the-loop
approval workflow, and loop monitoring. These tests never call a language
model - they call guardrails.py and approval_manager.py directly with
fixed inputs, so the same input always produces the same, predictable
result.

Run them with:
    python tests/test_guardrails.py
or, if you have pytest installed:
    pytest tests/test_guardrails.py
"""

import os
import sys

# Allow running this file directly (python tests/test_guardrails.py) by
# putting the project root - one directory up from this file - on the
# import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import approval_manager  # noqa: E402
import audit_logger  # noqa: E402
import guardrails  # noqa: E402
import observability  # noqa: E402
from tools import SIDE_EFFECTS  # noqa: E402


def _reset_state():
    """Start every test from a clean slate, so tests never affect each other."""
    approval_manager.reset()
    audit_logger.reset()
    observability.reset()
    SIDE_EFFECTS.clear()


def _new_trace(query: str = "test query") -> observability.ExecutionTrace:
    return observability.ExecutionTrace(agent_name="test_agent", query=query)


# 1. Allowed machine status check -> ALLOW
def test_allowed_machine_status_check():
    _reset_state()
    trace = _new_trace()
    result = guardrails.dispatch_action(trace, "CNC-01", "check_machine_status", {"machine_id": "CNC-01"})
    assert result["decision"] == "ALLOW"
    assert result["executed"] is True


# 2. Allowed maintenance history -> ALLOW
def test_allowed_maintenance_history():
    _reset_state()
    trace = _new_trace()
    result = guardrails.dispatch_action(trace, "ROBOT-03", "get_maintenance_history", {"machine_id": "ROBOT-03"})
    assert result["decision"] == "ALLOW"
    assert result["executed"] is True


# 3. Emergency machine shutdown -> REQUIRES_APPROVAL
def test_emergency_shutdown_requires_approval():
    _reset_state()
    trace = _new_trace()
    result = guardrails.dispatch_action(
        trace, "CNC-01", "stop_machine", {"machine_id": "CNC-01", "reason": "High vibration risk"}
    )
    assert result["decision"] == "REQUIRES_APPROVAL"
    assert result["executed"] is False
    assert len(SIDE_EFFECTS) == 0  # proposing the action must never run it


# 4. Human approves shutdown -> EXECUTED exactly once
def test_human_approves_shutdown_executes_once():
    _reset_state()
    trace = _new_trace()
    proposal = guardrails.dispatch_action(
        trace, "CNC-01", "stop_machine", {"machine_id": "CNC-01", "reason": "High vibration risk"}
    )
    action_id = proposal["action_id"]

    outcome = approval_manager.approve(action_id, reviewer="safety_officer")
    assert outcome["status"] == "executed"
    assert len(SIDE_EFFECTS) == 1

    checkpoint = approval_manager.get_checkpoint(action_id)
    assert checkpoint["status"] == approval_manager.EXECUTED


# 5. Human rejects shutdown -> no execution
def test_human_rejects_shutdown_blocks_execution():
    _reset_state()
    trace = _new_trace()
    proposal = guardrails.dispatch_action(
        trace, "CNC-01", "stop_machine", {"machine_id": "CNC-01", "reason": "High vibration risk"}
    )
    action_id = proposal["action_id"]

    outcome = approval_manager.reject(action_id, reviewer="safety_officer")
    assert outcome["status"] == "rejected"
    assert len(SIDE_EFFECTS) == 0

    checkpoint = approval_manager.get_checkpoint(action_id)
    assert checkpoint["status"] == approval_manager.REJECTED


# 6. Delete machine data -> DENY
def test_delete_machine_data_denied():
    _reset_state()
    trace = _new_trace()
    result = guardrails.dispatch_action(trace, "CNC-01", "delete_machine_data", {"machine_id": "CNC-01"})
    assert result["decision"] == "DENY"
    assert result["executed"] is False
    assert len(SIDE_EFFECTS) == 0


# 7. Modify safety logs -> DENY
def test_modify_safety_logs_denied():
    _reset_state()
    trace = _new_trace()
    result = guardrails.dispatch_action(
        trace, "CNC-01", "modify_safety_logs", {"machine_id": "CNC-01", "entry": "tampered entry"}
    )
    assert result["decision"] == "DENY"
    assert result["executed"] is False
    assert len(SIDE_EFFECTS) == 0


# 8. Unknown tool -> DENY (default-deny policy)
def test_unknown_tool_denied():
    _reset_state()
    trace = _new_trace()
    result = guardrails.dispatch_action(trace, "CNC-01", "reformat_disk", {"machine_id": "CNC-01"})
    assert result["decision"] == "DENY"
    assert result["executed"] is False


# 9. Repeated execution attempt -> blocked
def test_repeated_execution_attempt_is_blocked():
    _reset_state()
    trace = _new_trace()
    proposal = guardrails.dispatch_action(
        trace, "CNC-01", "stop_machine", {"machine_id": "CNC-01", "reason": "High vibration risk"}
    )
    action_id = proposal["action_id"]

    first_attempt = approval_manager.approve(action_id, reviewer="safety_officer")
    second_attempt = approval_manager.approve(action_id, reviewer="safety_officer")

    assert first_attempt["status"] == "executed"
    assert second_attempt["status"] == "blocked"
    assert len(SIDE_EFFECTS) == 1  # only the first approval ever executed anything


# 10. Malformed arguments -> DENY
def test_malformed_arguments_denied():
    _reset_state()
    trace = _new_trace()
    # stop_machine requires both "machine_id" and "reason" - "reason" is missing here.
    result = guardrails.dispatch_action(trace, "CNC-01", "stop_machine", {"machine_id": "CNC-01"})
    assert result["decision"] == "DENY"
    assert result["executed"] is False


# 11. Loop monitoring: exceeding MAX_AGENT_STEPS -> SAFE_STOP
def test_max_agent_steps_triggers_safe_stop():
    _reset_state()
    trace = _new_trace()
    read_only_tools = ["check_machine_status", "get_maintenance_history", "predict_failure_risk"]
    machines = ["CNC-01", "ROBOT-03", "CONVEYOR-02"]
    combos = [(tool, machine) for tool in read_only_tools for machine in machines]  # 9 distinct combos

    last_result = None
    for tool_name, machine_id in combos[:7]:  # MAX_AGENT_STEPS is 6, so the 7th must be stopped
        last_result = guardrails.dispatch_action(trace, machine_id, tool_name, {"machine_id": machine_id})

    assert last_result["decision"] == "SAFE_STOP"
    safe_stop_events = [e for e in trace.events if e.event_type == "safe_stop"]
    assert safe_stop_events[-1].metadata["failure_type"] == "POTENTIAL_AGENT_LOOP"


# 12. Loop monitoring: identical repeated call -> SAFE_STOP, before the step budget is reached
def test_identical_repeated_call_triggers_safe_stop():
    _reset_state()
    trace = _new_trace()
    first = guardrails.dispatch_action(trace, "CNC-01", "check_machine_status", {"machine_id": "CNC-01"})
    second = guardrails.dispatch_action(trace, "CNC-01", "check_machine_status", {"machine_id": "CNC-01"})

    assert first["decision"] == "ALLOW"
    assert second["decision"] == "SAFE_STOP"


ALL_TESTS = [
    test_allowed_machine_status_check,
    test_allowed_maintenance_history,
    test_emergency_shutdown_requires_approval,
    test_human_approves_shutdown_executes_once,
    test_human_rejects_shutdown_blocks_execution,
    test_delete_machine_data_denied,
    test_modify_safety_logs_denied,
    test_unknown_tool_denied,
    test_repeated_execution_attempt_is_blocked,
    test_malformed_arguments_denied,
    test_max_agent_steps_triggers_safe_stop,
    test_identical_repeated_call_triggers_safe_stop,
]


def run_all_tests() -> None:
    """Run every test in order and print a PASS/FAIL summary."""
    passed = 0
    for test_function in ALL_TESTS:
        try:
            test_function()
            print(f"PASS - {test_function.__name__}")
            passed += 1
        except AssertionError as exc:
            print(f"FAIL - {test_function.__name__}: {exc}")
    print(f"\n{passed}/{len(ALL_TESTS)} guardrail tests passed.")


if __name__ == "__main__":
    run_all_tests()
