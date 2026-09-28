"""System health report over recorded agent runs.

Answers "is the platform healthy, what is failing, and what should we do?":

1. Failure classification: every failed run and failed tool call is put in
   one category (fixed severity order, most severe first).
2. Latency anomalies: a run or tool call is anomalous when it is slower than
   mean + 2 standard deviations of its own group (per agent for runs, per tool
   for tool calls), using only successful calls as the baseline.
3. Overall status: HEALTHY / DEGRADED / CRITICAL from explicit, documented
   rules, with the reasons that triggered it.
4. Recommended actions, derived from what actually occurred in the window.

The baseline is the window itself, so a longer window gives a steadier
baseline, and rate-based rules are skipped below MIN_RUNS to avoid one bad
run flipping the status.
"""
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.observability.metrics import load_window, rate

HEALTHY, DEGRADED, CRITICAL = "HEALTHY", "DEGRADED", "CRITICAL"

MIN_RUNS = 5  # below this, success-rate rules are not applied
MIN_BASELINE_SAMPLES = 5  # below this, no latency baseline is computed
CRITICAL_SUCCESS_RATE = 0.70
DEGRADED_SUCCESS_RATE = 0.90
TOOL_ERROR_RATE_DEGRADED = 0.10
TOOL_MIN_CALLS = 5
ANOMALY_STD_MULTIPLIER = 2
MAX_LISTED_ANOMALIES = 10

# (name, severity, what it means, recommended action) - most severe first.
CATEGORIES: Tuple[Tuple[str, str, str, str], ...] = (
    (
        "CONFIGURATION",
        "critical",
        "Missing or invalid credentials or settings",
        "Check OPENAI_API_KEY, the Supabase keys, and other required environment variables; "
        "rotate the key if it was revoked.",
    ),
    (
        "PROVIDER_UNAVAILABLE",
        "high",
        "OpenAI API errors: rate limits, timeouts, connection or 5xx failures",
        "Check OpenAI status and quota; add retry with backoff, or fail over to another model.",
    ),
    (
        "TOOL_FAILURE",
        "high",
        "Agent tools (policy repository, risk rules, document analysis) returning errors",
        "Check the database and the failing tool. Agents continue without that evidence, so "
        "affected compliance findings may be unverified.",
    ),
    (
        "OUTPUT_CONTRACT",
        "medium",
        "Agent output was not valid JSON for its response schema",
        "Review the prompt and response schema for the failing agent; consider a model with "
        "stronger structured-output support.",
    ),
    (
        "LOOP_LIMIT",
        "medium",
        "Agent hit its tool-iteration limit without a final answer",
        "Look for repeated tool calls in the failed traces and tighten the prompt; raise "
        "GOVERNAI_MAX_TOOL_ITERATIONS only if traces show legitimately long chains.",
    ),
    (
        "UNKNOWN",
        "medium",
        "Failures that match no known pattern",
        "Inspect the failed runs' error_message in agent_runs and add a classification rule.",
    ),
)
SEVERITY = {name: severity for name, severity, _, _ in CATEGORIES}

_CONFIG_TYPES = {"ConfigurationError", "SupabaseNotConfiguredError", "AuthenticationError", "PermissionDeniedError"}
_CONFIG_TEXT = ("api key", "api_key", "not configured", "not set", "authentication")
_PROVIDER_TYPES = {
    "RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError",
    "APIStatusError", "TimeoutError", "ConnectTimeout", "ReadTimeout", "ConnectError",
}
_PROVIDER_TEXT = ("rate limit", "timed out", "timeout", "connection error", "overloaded", "503", "502")
_TOOL_ERROR_TEXT = re.compile(r'"error":\s*"([^"]{1,120})')


def classify_run_error(error_type: Optional[str], message: Optional[str]) -> str:
    """Category for a failed run. Checked in severity order, first match wins."""
    kind = error_type or ""
    text = (message or "").lower()
    if kind in _CONFIG_TYPES or any(fragment in text for fragment in _CONFIG_TEXT):
        return "CONFIGURATION"
    if "exceeded max_tool_iterations" in text:
        return "LOOP_LIMIT"
    if kind in _PROVIDER_TYPES or any(fragment in text for fragment in _PROVIDER_TEXT):
        return "PROVIDER_UNAVAILABLE"
    if kind in {"AgentError", "ValidationError", "JSONDecodeError"}:
        return "OUTPUT_CONTRACT"
    return "UNKNOWN"


def _tool_error_text(call: Dict[str, Any]) -> str:
    preview = str(call.get("output_preview") or "")
    match = _TOOL_ERROR_TEXT.search(preview)
    return match.group(1) if match else preview[:100]


def _baseline(values: List[float]) -> Optional[Tuple[float, float, float]]:
    """(mean, std, threshold) or None when there are too few samples."""
    if len(values) < MIN_BASELINE_SAMPLES:
        return None
    mean, std = statistics.fmean(values), statistics.stdev(values)
    return mean, std, mean + ANOMALY_STD_MULTIPLIER * std


def _classify_failures(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    affected: Dict[str, List[str]] = defaultdict(list)  # category -> run ids
    tool_calls_affected: Counter = Counter()
    examples: Dict[str, Counter] = defaultdict(Counter)

    for row in runs:
        run_id = row.get("id")
        if row.get("status") == "error":
            category = classify_run_error(row.get("error_type"), row.get("error_message"))
            affected[category].append(run_id)
            examples[category][row.get("error_type") or "unknown"] += 1
        failed_calls = [call for call in row.get("tool_calls") or [] if not call.get("ok", True)]
        if failed_calls:
            affected["TOOL_FAILURE"].append(run_id)
            for call in failed_calls:
                tool_calls_affected["TOOL_FAILURE"] += 1
                examples["TOOL_FAILURE"][f"{call.get('tool')}: {_tool_error_text(call)}"] += 1

    categories = []
    for name, severity, description, action in CATEGORIES:
        run_ids = list(dict.fromkeys(affected.get(name, [])))
        categories.append(
            {
                "category": name,
                "severity": severity,
                "description": description,
                "action": action,
                "runs_affected": len(run_ids),
                "tool_calls_affected": tool_calls_affected.get(name, 0),
                "top_errors": [{"error": err, "count": n} for err, n in examples[name].most_common(3)],
                "dominant": False,
            }
        )
    hits = [item for item in categories if item["runs_affected"]]
    if hits:
        max(hits, key=lambda item: item["runs_affected"])["dominant"] = True
    return categories


def _latency_anomalies(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    run_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    tool_groups: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = defaultdict(list)
    for row in runs:
        if row.get("status") == "success":
            run_groups[row.get("agent") or "unknown"].append(row)
        for call in row.get("tool_calls") or []:
            if call.get("ok", True):
                tool_groups[call.get("tool") or "unknown"].append((row, call))

    groups, flagged = [], []

    def add_group(kind: str, name: str, samples: List[float], flagged_items: List[Dict[str, Any]], base) -> None:
        mean, std, threshold = base
        groups.append(
            {
                "kind": kind,
                "name": name,
                "samples": len(samples),
                "mean_ms": round(mean, 1),
                "std_ms": round(std, 1),
                "threshold_ms": round(threshold, 1),
                "anomalies": len(flagged_items),
                "anomaly_rate": rate(len(flagged_items), len(samples)),
            }
        )
        flagged.extend(flagged_items)

    for agent, rows in sorted(run_groups.items()):
        values = [float(row.get("latency_ms") or 0) for row in rows]
        base = _baseline(values)
        if base:
            items = [
                {
                    "kind": "agent_run",
                    "name": agent,
                    "run_id": row.get("id"),
                    "use_case_id": row.get("use_case_id"),
                    "started_at": row.get("started_at"),
                    "latency_ms": float(row.get("latency_ms") or 0),
                    "threshold_ms": round(base[2], 1),
                }
                for row in rows
                if float(row.get("latency_ms") or 0) > base[2]
            ]
            add_group("agent_run", agent, values, items, base)

    for tool, pairs in sorted(tool_groups.items()):
        values = [float(call.get("latency_ms") or 0) for _, call in pairs]
        base = _baseline(values)
        if base:
            items = [
                {
                    "kind": "tool_call",
                    "name": tool,
                    "run_id": row.get("id"),
                    "use_case_id": row.get("use_case_id"),
                    "started_at": row.get("started_at"),
                    "latency_ms": float(call.get("latency_ms") or 0),
                    "threshold_ms": round(base[2], 1),
                }
                for row, call in pairs
                if float(call.get("latency_ms") or 0) > base[2]
            ]
            add_group("tool_call", tool, values, items, base)

    flagged.sort(key=lambda item: item["latency_ms"] / max(item["threshold_ms"], 1), reverse=True)
    return {
        "method": f"latency > mean + {ANOMALY_STD_MULTIPLIER} x std of successful calls in the same group",
        "total": len(flagged),
        "groups": groups,
        "top": flagged[:MAX_LISTED_ANOMALIES],
    }


def _worst_component(runs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidates = []

    agent_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    tool_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in runs:
        agent_rows[row.get("agent") or "unknown"].append(row)
        for call in row.get("tool_calls") or []:
            tool_rows[call.get("tool") or "unknown"].append(call)

    for agent, rows in agent_rows.items():
        failed = [row for row in rows if row.get("status") == "error"]
        if len(rows) >= 3 and failed:
            top = Counter(row.get("error_type") or "unknown" for row in failed).most_common(1)[0][0]
            candidates.append(("agent", agent, len(failed), len(rows), top))
    for tool, calls in tool_rows.items():
        failed = [call for call in calls if not call.get("ok", True)]
        if len(calls) >= 3 and failed:
            top = Counter(_tool_error_text(call) for call in failed).most_common(1)[0][0]
            candidates.append(("tool", tool, len(failed), len(calls), top))

    if not candidates:
        return None
    kind, name, failed, total, top = max(candidates, key=lambda c: (c[2] / c[3], c[3]))
    return {"kind": kind, "name": name, "failure_rate": rate(failed, total), "failed": failed, "total": total, "top_error": top}


def build_health_report(
    runs: List[Dict[str, Any]],
    window: Dict[str, Any],
    generated_at: Optional[datetime] = None,
    truncated: bool = False,
) -> Dict[str, Any]:
    total = len(runs)
    errors = sum(1 for row in runs if row.get("status") == "error")
    success_rate = rate(total - errors, total)
    enough = total >= MIN_RUNS

    categories = _classify_failures(runs)
    by_name = {item["category"]: item for item in categories}
    anomalies = _latency_anomalies(runs)
    worst = _worst_component(runs)

    tool_totals: Dict[str, List[int]] = defaultdict(lambda: [0, 0])  # tool -> [calls, errors]
    for row in runs:
        for call in row.get("tool_calls") or []:
            counts = tool_totals[call.get("tool") or "unknown"]
            counts[0] += 1
            counts[1] += 0 if call.get("ok", True) else 1
    noisy_tools = sorted(
        (
            {"tool": tool, "error_rate": rate(errs, calls), "calls": calls}
            for tool, (calls, errs) in tool_totals.items()
            if calls >= TOOL_MIN_CALLS and errs / calls >= TOOL_ERROR_RATE_DEGRADED
        ),
        key=lambda item: -item["error_rate"],
    )

    status, reasons = HEALTHY, []

    def raise_to(level: str, reason: str) -> None:
        nonlocal status
        order = {HEALTHY: 0, DEGRADED: 1, CRITICAL: 2}
        if order[level] > order[status]:
            status = level
        reasons.append(f"{level}: {reason}")

    if by_name["CONFIGURATION"]["runs_affected"]:
        raise_to(CRITICAL, f"{by_name['CONFIGURATION']['runs_affected']} run(s) failed on configuration or credentials")
    if enough and success_rate < CRITICAL_SUCCESS_RATE:
        raise_to(CRITICAL, f"success rate {success_rate:.1%} is below {CRITICAL_SUCCESS_RATE:.0%}")
    elif enough and success_rate < DEGRADED_SUCCESS_RATE:
        raise_to(DEGRADED, f"success rate {success_rate:.1%} is below {DEGRADED_SUCCESS_RATE:.0%}")
    for name in ("PROVIDER_UNAVAILABLE", "TOOL_FAILURE"):
        if by_name[name]["runs_affected"] >= 2:
            raise_to(DEGRADED, f"{by_name[name]['runs_affected']} runs affected by {name}")
    for tool in noisy_tools:
        raise_to(DEGRADED, f"tool {tool['tool']} error rate {tool['error_rate']:.1%} over {tool['calls']} calls")

    notes = []
    if total == 0:
        notes.append("No agent runs in this window, so health cannot be assessed.")
    elif not enough:
        notes.append(f"Only {total} run(s) in this window; success-rate rules need at least {MIN_RUNS}.")
    if truncated:
        notes.append(f"Only the {total} most recent runs were analysed (row limit reached).")
    if not anomalies["groups"] and total:
        notes.append(f"Latency baselines need at least {MIN_BASELINE_SAMPLES} successful samples per agent or tool.")

    actions: List[Dict[str, Any]] = []
    for item in categories:
        if item["runs_affected"]:
            actions.append({"category": item["category"], "severity": item["severity"], "action": item["action"],
                            "reason": f"{item['runs_affected']} run(s) affected"})
    for group in anomalies["groups"]:
        if group["anomalies"]:
            actions.append(
                {
                    "category": "LATENCY_ANOMALY",
                    "severity": "medium",
                    "action": f"Investigate slow {group['kind'].replace('_', ' ')}s for {group['name']}: "
                              f"check the provider or dependency latency around the flagged runs.",
                    "reason": f"{group['anomalies']} of {group['samples']} exceeded {group['threshold_ms']:.0f} ms "
                              f"(mean {group['mean_ms']:.0f} ms + {ANOMALY_STD_MULTIPLIER} std)",
                }
            )
    for position, action in enumerate(actions, 1):
        action["priority"] = position

    generated = generated_at or datetime.now(timezone.utc)
    return {
        "status": status,
        "generated_at": generated.isoformat(),
        "window": window,
        "reasons": reasons,
        "notes": notes,
        "data_sufficient": enough,
        "summary": {
            "runs": total,
            "success": total - errors,
            "error": errors,
            "success_rate": success_rate,
            "runs_with_tool_failures": by_name["TOOL_FAILURE"]["runs_affected"],
        },
        "failure_categories": categories,
        "worst_component": worst,
        "latency_anomalies": anomalies,
        "recommended_actions": actions,
    }


def health_for_window(hours: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    data = load_window(hours, now)
    return build_health_report(data["runs"], data["window"], truncated=data["truncated"])
