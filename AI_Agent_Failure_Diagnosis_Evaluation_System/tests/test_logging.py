"""
tests/test_logging.py
-----------------------
Tests for the observability, performance monitoring, and log analysis
layer: ExecutionTrace records the events it is given, PerformanceMonitor
computes correct aggregate numbers from a small fabricated set of traces,
and log_analyzer.analyze() detects the issues it claims to detect.

Run them with:
    python tests/test_logging.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import observability  # noqa: E402
from performance_monitor import PerformanceMonitor  # noqa: E402
from log_analyzer import analyze  # noqa: E402


def _reset_state():
    observability.reset()


def test_execution_trace_has_a_unique_trace_id():
    _reset_state()
    trace_a = observability.ExecutionTrace(agent_name="test_agent", query="a")
    trace_b = observability.ExecutionTrace(agent_name="test_agent", query="b")
    assert trace_a.trace_id != trace_b.trace_id


def test_execution_trace_records_events_in_order():
    _reset_state()
    trace = observability.ExecutionTrace(agent_name="test_agent", query="Check CNC-01")
    trace.log_event("tool_call", tool_name="check_machine_status", tool_arguments={"machine_id": "CNC-01"})
    trace.log_event("tool_result", tool_name="check_machine_status", result="ok", status="success")
    trace.finish("success", {"diagnosis": "ok"})

    trace_dict = trace.to_dict()
    event_types = [event["event_type"] for event in trace_dict["steps"]]
    assert event_types == ["agent_start", "tool_call", "tool_result", "final_result"]
    assert trace_dict["status"] == "success"


def test_execution_trace_exports_valid_json():
    _reset_state()
    trace = observability.ExecutionTrace(agent_name="test_agent", query="Check CNC-01")
    trace.finish("success", {"diagnosis": "ok"})
    exported = trace.to_json()
    assert '"trace_id"' in exported
    assert '"steps"' in exported


def _fabricated_trace(status: str, total_latency_ms: float, tool_events: list) -> dict:
    """Build a trace dictionary by hand, without running a real agent - useful for
    testing PerformanceMonitor in isolation with exact, known numbers."""
    steps = [{"event_type": "agent_start", "step_number": 1, "tool_name": None, "latency_ms": 0.0}]
    for i, (event_type, tool_name, latency_ms) in enumerate(tool_events, start=2):
        steps.append({"event_type": event_type, "step_number": i, "tool_name": tool_name, "latency_ms": latency_ms})
    return {"trace_id": "fake", "status": status, "total_latency_ms": total_latency_ms, "steps": steps}


def test_performance_monitor_success_and_error_rate():
    traces = [
        _fabricated_trace("completed", 100.0, [("tool_call", "check_machine_status", 0.0), ("tool_result", "check_machine_status", 20.0)]),
        _fabricated_trace("completed", 50.0, [("tool_call", "check_machine_status", 0.0), ("tool_result", "check_machine_status", 10.0)]),
        _fabricated_trace("safe_stop", 30.0, [("tool_call", "check_machine_status", 0.0)]),
    ]
    summary = PerformanceMonitor(traces).summary()
    assert summary["total_runs"] == 3
    assert summary["failed_runs"] == 1
    assert summary["successful_runs"] == 2
    assert summary["error_rate"] == round(100 / 3, 1)


def test_performance_monitor_latency_percentiles():
    traces = [_fabricated_trace("completed", latency, []) for latency in [10.0, 20.0, 30.0, 40.0, 50.0]]
    summary = PerformanceMonitor(traces).summary()
    assert summary["avg_latency_ms"] == 30.0
    assert summary["p50_latency_ms"] == 30.0
    # With 5 values, the nearest-rank P95 lands on the highest value.
    assert summary["p95_latency_ms"] == 50.0


def test_performance_monitor_tool_metrics():
    traces = [
        _fabricated_trace("completed", 20.0, [
            ("guardrail_decision", "check_machine_status", 0.0),
            ("tool_result", "check_machine_status", 20.0),
        ]),
        _fabricated_trace("safe_stop", 0.0, [
            ("guardrail_decision", "check_machine_status", 0.0),
        ]),
    ]
    metrics = PerformanceMonitor(traces).tool_metrics()
    assert metrics["check_machine_status"]["calls"] == 2
    assert metrics["check_machine_status"]["successful_calls"] == 1
    assert metrics["check_machine_status"]["failed_calls"] == 1
    assert metrics["check_machine_status"]["success_rate"] == 50.0


def test_log_analyzer_detects_repeated_tool_calls_and_denials():
    audit_log = [
        {"requested_tool": "check_machine_status", "arguments": {"machine_id": "CNC-01"}, "policy_decision": "ALLOW",
         "executed": True, "reason": "read-only", "latency_ms": 10.0},
        {"requested_tool": "check_machine_status", "arguments": {"machine_id": "CNC-01"}, "policy_decision": "ALLOW",
         "executed": True, "reason": "read-only", "latency_ms": 10.0},
        {"requested_tool": "delete_machine_data", "arguments": {"machine_id": "CNC-01"}, "policy_decision": "DENY",
         "executed": False, "reason": "forbidden", "latency_ms": 0.0},
        {"requested_tool": "reformat_disk", "arguments": {"machine_id": "CNC-01"}, "policy_decision": "DENY",
         "executed": False, "reason": "Unknown tool 'reformat_disk': default-deny policy applies.", "latency_ms": 0.0},
    ]
    findings = analyze(audit_log, traces=[])
    issues = {finding["issue"] for finding in findings}
    assert "Repeated tool calls" in issues
    assert "Denied guardrail actions" in issues
    assert "Invalid tool requests" in issues


ALL_TESTS = [
    test_execution_trace_has_a_unique_trace_id,
    test_execution_trace_records_events_in_order,
    test_execution_trace_exports_valid_json,
    test_performance_monitor_success_and_error_rate,
    test_performance_monitor_latency_percentiles,
    test_performance_monitor_tool_metrics,
    test_log_analyzer_detects_repeated_tool_calls_and_denials,
]


def run_all_tests() -> None:
    passed = 0
    for test_function in ALL_TESTS:
        try:
            test_function()
            print(f"PASS - {test_function.__name__}")
            passed += 1
        except AssertionError as exc:
            print(f"FAIL - {test_function.__name__}: {exc}")
    print(f"\n{passed}/{len(ALL_TESTS)} logging tests passed.")


if __name__ == "__main__":
    run_all_tests()
