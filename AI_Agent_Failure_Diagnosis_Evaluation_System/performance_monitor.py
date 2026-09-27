"""
performance_monitor.py
------------------------
Aggregates ExecutionTrace data (observability.py) into the KPIs shown on
the "System Monitoring" dashboard page and used for the Baseline vs
Improved performance comparison: run-level success/failure rates, latency
percentiles, and tool-call counts, plus per-tool metrics.

Everything here is computed from already-logged, observable data (trace
dictionaries, as produced by ExecutionTrace.to_dict()) - no extra
instrumentation is needed anywhere else in the project.
"""

from typing import Any, Dict, List

# A run counts as "failed" if it ended in one of these states.
FAILED_STATUSES = {"error", "safe_stop"}


def _percentile(values: List[float], percentile: float) -> float:
    """A small, dependency-free percentile calculator (nearest-rank method)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(percentile / 100 * (len(ordered) - 1)))))
    return round(ordered[index], 2)


class PerformanceMonitor:
    """Computes run-level and per-tool metrics from a list of trace dictionaries."""

    def __init__(self, traces: List[Dict[str, Any]]):
        self.traces = traces

    # --- run-level KPIs --------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Total/successful/failed runs, latency percentiles, and tool-call/step averages."""
        total = len(self.traces)
        if total == 0:
            return {
                "total_runs": 0, "successful_runs": 0, "failed_runs": 0,
                "success_rate": 0.0, "error_rate": 0.0, "avg_latency_ms": 0.0,
                "p50_latency_ms": 0.0, "p95_latency_ms": 0.0, "p99_latency_ms": 0.0,
                "avg_tool_calls": 0.0, "max_tool_calls": 0, "avg_steps": 0.0,
            }

        latencies = [trace.get("total_latency_ms", 0.0) for trace in self.traces]
        tool_call_counts = [self._tool_call_count(trace) for trace in self.traces]
        step_counts = [self._step_count(trace) for trace in self.traces]
        failed = sum(1 for trace in self.traces if trace.get("status") in FAILED_STATUSES)
        successful = total - failed

        return {
            "total_runs": total,
            "successful_runs": successful,
            "failed_runs": failed,
            "success_rate": round(100 * successful / total, 1),
            "error_rate": round(100 * failed / total, 1),
            "avg_latency_ms": round(sum(latencies) / total, 2),
            "p50_latency_ms": _percentile(latencies, 50),
            "p95_latency_ms": _percentile(latencies, 95),
            "p99_latency_ms": _percentile(latencies, 99),
            "avg_tool_calls": round(sum(tool_call_counts) / total, 2),
            "max_tool_calls": max(tool_call_counts),
            "avg_steps": round(sum(step_counts) / total, 2),
        }

    # --- per-tool KPIs -----------------------------------------------------

    def tool_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Number of calls, success/error rate, and latency, broken down by tool name."""
        per_tool: Dict[str, Dict[str, Any]] = {}
        for trace in self.traces:
            for event in trace.get("steps", []):
                tool = event.get("tool_name")
                if not tool:
                    continue
                bucket = per_tool.setdefault(tool, {"attempts": 0, "successes": 0, "latencies": []})
                if event["event_type"] == "guardrail_decision":
                    bucket["attempts"] += 1
                elif event["event_type"] == "tool_result":
                    bucket["successes"] += 1
                    bucket["latencies"].append(event.get("latency_ms", 0.0))

        metrics = {}
        for tool, bucket in per_tool.items():
            attempts = bucket["attempts"]
            successes = bucket["successes"]
            failures = max(attempts - successes, 0)
            latencies = bucket["latencies"]
            metrics[tool] = {
                "calls": attempts,
                "successful_calls": successes,
                "failed_calls": failures,
                "success_rate": round(100 * successes / attempts, 1) if attempts else 0.0,
                "error_rate": round(100 * failures / attempts, 1) if attempts else 0.0,
                "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
                "p95_latency_ms": _percentile(latencies, 95),
            }
        return metrics

    # --- helpers -------------------------------------------------------

    @staticmethod
    def _tool_call_count(trace: Dict[str, Any]) -> int:
        return sum(1 for event in trace.get("steps", []) if event["event_type"] == "tool_call")

    @staticmethod
    def _step_count(trace: Dict[str, Any]) -> int:
        """
        Every *attempted* tool call (successful or blocked) counts as one
        step - the same thing MAX_AGENT_STEPS budgets against. This is
        deliberately different from avg_tool_calls, which (in evaluator.py)
        only counts calls that actually executed - the gap between the two
        shows how many attempts were blocked.
        """
        return sum(1 for event in trace.get("steps", []) if event["event_type"] == "tool_call")
