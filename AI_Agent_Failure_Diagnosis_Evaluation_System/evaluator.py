"""
evaluator.py
------------
Runs the frozen evaluation dataset (evaluation_dataset.py) against the
agent (agent.py) and scores each result against transparent, observable
checks: tool calls, tool results (evidence), required concepts in the
final response, and forbidden claims. Nothing here inspects private model
reasoning - every check is based only on the agent's logged, observable
behavior.

Each evaluated case also has its own ExecutionTrace (observability.py),
so summarize() can additionally report latency percentiles, guardrail
violations, and loop events for that batch of runs, using the same
PerformanceMonitor class the "System Monitoring" dashboard page uses.
"""

from typing import Any, Dict, List

import observability
from agent import run_diagnosis
from failure_detector import detect_failures
from performance_monitor import PerformanceMonitor


def _response_text(result: Dict[str, Any]) -> str:
    """The text we run concept/claim checks against: diagnosis + recommendation."""
    return f"{result['diagnosis']} {result['recommendation']}".lower()


def evaluate_case(case: Dict[str, Any], agent_version: str) -> Dict[str, Any]:
    """Run one evaluation case against one agent version and score the result."""
    result = run_diagnosis(case["query"], agent_version=agent_version)
    response_text = _response_text(result)
    tool_calls = result["tool_calls"]
    evidence = result["evidence"]

    checks = {
        "required_tools_used": all(tool in tool_calls for tool in case["required_tools"]),
        "no_forbidden_tools": not any(tool in tool_calls for tool in case["forbidden_tools"]),
        "required_evidence_found": all(item in evidence for item in case["required_evidence"]),
        "required_concepts_found": all(
            any(phrase.lower() in response_text for phrase in group) for group in case["required_concepts"]
        ),
        "no_forbidden_claims": not any(claim.lower() in response_text for claim in case["forbidden_claims"]),
        "within_tool_call_budget": len(tool_calls) <= case["max_tool_calls"],
    }
    passed = all(checks.values())

    return {
        "id": case["id"],
        "category": case["category"],
        "difficulty": case["difficulty"],
        "query": case["query"],
        "expected_behavior": case["expected_behavior"],
        "agent_version": agent_version,
        "passed": passed,
        "checks": checks,
        "tool_calls": tool_calls,
        "num_tool_calls": len(tool_calls),
        "latency_ms": result["latency_ms"],
        "trace_id": result["trace_id"],
        "actual_response": f"{result['diagnosis']} {result['recommendation']}".strip(),
        "failures_detected": detect_failures(result),
    }


def run_evaluation(cases: List[Dict[str, Any]], agent_version: str) -> List[Dict[str, Any]]:
    """Run every case in `cases` against one agent version."""
    return [evaluate_case(case, agent_version) for case in cases]


def _traces_for(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Look up the full ExecutionTrace for every case in this batch."""
    traces = []
    for result in results:
        trace = observability.get_trace(result["trace_id"])
        if trace is not None:
            traces.append(trace.to_dict())
    return traces


def _count_guardrail_violations(traces: List[Dict[str, Any]]) -> int:
    """How many proposed actions were denied by the guardrail policy across this batch."""
    return sum(
        1 for trace in traces for event in trace["steps"]
        if event["event_type"] == "guardrail_decision" and event["status"] == "DENY"
    )


def _count_loop_events(traces: List[Dict[str, Any]]) -> int:
    """How many runs were force-stopped for a potential agent loop across this batch."""
    return sum(1 for trace in traces for event in trace["steps"] if event["event_type"] == "safe_stop")


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Turn a list of per-case results into the aggregate metrics the dashboard shows."""
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    tool_accuracy_hits = sum(
        1 for r in results if r["checks"]["required_tools_used"] and r["checks"]["no_forbidden_tools"]
    )
    failed_categories = sorted({r["category"] for r in results if not r["passed"]})

    traces = _traces_for(results)
    performance = PerformanceMonitor(traces).summary()

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(100 * passed / total, 1) if total else 0.0,
        "tool_accuracy": round(100 * tool_accuracy_hits / total, 1) if total else 0.0,
        "avg_tool_calls": round(sum(r["num_tool_calls"] for r in results) / total, 2) if total else 0.0,
        "avg_latency_ms": round(sum(r["latency_ms"] for r in results) / total, 2) if total else 0.0,
        "failed_categories": failed_categories,
        # Metrics added for the Guardrails/Observability layer:
        "error_rate": performance["error_rate"],
        "p95_latency_ms": performance["p95_latency_ms"],
        "avg_steps": performance["avg_steps"],
        "guardrail_violations": _count_guardrail_violations(traces),
        "loop_events": _count_loop_events(traces),
    }


def compare_versions(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Run the same frozen dataset against both the baseline and the improved
    agent, and return both sets of results plus their summaries, ready for
    the "Baseline vs Improved" dashboard page.
    """
    baseline_results = run_evaluation(cases, "baseline")
    improved_results = run_evaluation(cases, "improved")

    return {
        "baseline_results": baseline_results,
        "improved_results": improved_results,
        "baseline_summary": summarize(baseline_results),
        "improved_summary": summarize(improved_results),
    }
