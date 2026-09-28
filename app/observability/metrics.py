"""Aggregate metrics over recorded agent runs.

Follows the RED method used for services: Rate (how many runs), Errors
(what fraction fail), Duration (how long they take, as avg and P95), plus
per-tool usage, token cost, and the mix of governance decisions.

`compute_metrics` is a pure function over row dicts so it can be tested
without a database; `load_window` reads the rows for a time window.
"""
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from app import db

# Supabase's default cap on rows per request. A window with more runs than
# this is reported as `truncated` rather than silently under-counted.
MAX_ROWS = 1000

DECISIONS = ("approve", "require_human_approval", "block")


def parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp (with 'Z' or an offset); naive values are UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def percentile(values: Iterable[float], pct: float) -> float:
    """Nearest-rank percentile: the smallest value with at least `pct`% of
    the data at or below it. 0.0 for no data."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def latency_summary(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    return {
        "avg_ms": round(statistics.fmean(values), 1),
        "p50_ms": round(percentile(values, 50), 1),
        "p95_ms": round(percentile(values, 95), 1),
        "max_ms": round(max(values), 1),
    }


def _num(row: Dict[str, Any], key: str) -> float:
    return float(row.get(key) or 0)


def window_bounds(hours: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    until = now or datetime.now(timezone.utc)
    return {"hours": hours, "since": (until - timedelta(hours=hours)).isoformat(), "until": until.isoformat()}


def load_window(hours: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Agent runs and governance reports started/created inside the window."""
    window = window_bounds(hours, now)
    since = parse_ts(window["since"])

    def within(row: Dict[str, Any], key: str) -> bool:
        ts = parse_ts(row.get(key))
        return ts is not None and ts >= since

    run_rows = db.select(
        "agent_runs", {"started_at": f"gte.{window['since']}", "order": "started_at.desc", "limit": MAX_ROWS}
    )
    report_rows = db.select(
        "governance_reports", {"created_at": f"gte.{window['since']}", "limit": MAX_ROWS}
    )
    return {
        "window": window,
        "runs": [row for row in run_rows if within(row, "started_at")],
        "reports": [row for row in report_rows if within(row, "created_at")],
        "truncated": len(run_rows) >= MAX_ROWS or len(report_rows) >= MAX_ROWS,
    }


def compute_metrics(
    runs: List[Dict[str, Any]],
    reports: List[Dict[str, Any]],
    window: Dict[str, Any],
    truncated: bool = False,
) -> Dict[str, Any]:
    successes = [row for row in runs if row.get("status") == "success"]

    by_agent: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_model: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    tool_calls: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in runs:
        by_agent[row.get("agent") or "unknown"].append(row)
        by_model[row.get("model") or "unknown"].append(row)
        for call in row.get("tool_calls") or []:
            tool_calls[call.get("tool") or "unknown"].append(call)

    agents = []
    for name, rows in sorted(by_agent.items()):
        ok = sum(1 for row in rows if row.get("status") == "success")
        agents.append(
            {
                "agent": name,
                "runs": len(rows),
                "success_rate": rate(ok, len(rows)),
                **latency_summary([_num(row, "latency_ms") for row in rows]),
                "avg_tool_calls": round(statistics.fmean(_num(row, "tool_call_count") for row in rows), 2),
                "total_tokens": int(sum(_num(row, "total_tokens") for row in rows)),
                "cost_usd": round(sum(_num(row, "estimated_cost_usd") for row in rows), 6),
            }
        )

    tools = []
    for name, calls in tool_calls.items():
        errors = sum(1 for call in calls if not call.get("ok", True))
        latencies = [float(call.get("latency_ms") or 0) for call in calls]
        summary = latency_summary(latencies)
        tools.append(
            {
                "tool": name,
                "calls": len(calls),
                "errors": errors,
                "error_rate": rate(errors, len(calls)),
                "avg_latency_ms": summary["avg_ms"],
                "p95_latency_ms": summary["p95_ms"],
            }
        )
    tools.sort(key=lambda item: (-item["calls"], item["tool"]))

    total_cost = round(sum(_num(row, "estimated_cost_usd") for row in runs), 6)
    decision_counts = {decision: 0 for decision in DECISIONS}
    for report in reports:
        decision = report.get("decision")
        decision_counts[decision] = decision_counts.get(decision, 0) + 1

    return {
        "window": window,
        "truncated": truncated,
        "runs": {
            "total": len(runs),
            "success": len(successes),
            "error": len(runs) - len(successes),
            "success_rate": rate(len(successes), len(runs)),
        },
        "latency": latency_summary([_num(row, "latency_ms") for row in runs]),
        "tokens": {
            "prompt": int(sum(_num(row, "prompt_tokens") for row in runs)),
            "completion": int(sum(_num(row, "completion_tokens") for row in runs)),
            "total": int(sum(_num(row, "total_tokens") for row in runs)),
        },
        "cost": {
            "total_usd": total_cost,
            "avg_per_run_usd": round(total_cost / len(runs), 6) if runs else 0.0,
            "by_model": {
                model: round(sum(_num(row, "estimated_cost_usd") for row in rows), 6)
                for model, rows in sorted(by_model.items())
            },
            "note": "Estimated from token counts and a built-in price table; not a bill.",
        },
        "agents": agents,
        "tools": tools,
        "decisions": {
            "total": len(reports),
            "counts": decision_counts,
            "shares": {key: rate(count, len(reports)) for key, count in decision_counts.items()},
        },
    }


def metrics_for_window(hours: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    data = load_window(hours, now)
    return compute_metrics(data["runs"], data["reports"], data["window"], data["truncated"])
