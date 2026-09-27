"""
log_analyzer.py
-----------------
Automatic analysis of the audit log and execution traces: scans for
recurring problems and produces a short, plain-English explanation for
each one. This is a summary tool for a human reviewing the system - it
does not enforce anything itself (guardrails.py already does that); it
only helps someone notice a pattern worth investigating.

Detected issue categories:
    - repeated tool calls
    - high latency
    - failed tool executions
    - denied guardrail actions
    - rejected human approvals
    - excessive agent steps / loop events
    - invalid tool requests
    - premature answers
"""

from collections import Counter
from typing import Any, Dict, List

# A single tool call slower than this is flagged as "high latency".
HIGH_LATENCY_THRESHOLD_MS = 60.0


def analyze(audit_log: List[Dict[str, Any]], traces: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Return a list of {"issue": ..., "explanation": ...} findings."""
    findings: List[Dict[str, str]] = []
    findings.extend(_check_repeated_tool_calls(audit_log))
    findings.extend(_check_high_latency(audit_log))
    findings.extend(_check_failed_tool_executions(audit_log))
    findings.extend(_check_denied_actions(audit_log))
    findings.extend(_check_rejected_approvals(audit_log))
    findings.extend(_check_excessive_steps(audit_log))
    findings.extend(_check_invalid_tool_requests(audit_log))
    findings.extend(_check_premature_answers(traces))
    return findings


def _argument_signature(arguments: Dict[str, Any]) -> tuple:
    return tuple(sorted((arguments or {}).items()))


def _check_repeated_tool_calls(audit_log: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    seen = Counter((entry["requested_tool"], _argument_signature(entry["arguments"])) for entry in audit_log)
    return [
        {
            "issue": "Repeated tool calls",
            "explanation": (
                f"'{tool}' was called with the exact same arguments {count} times. "
                "Repeated identical calls usually mean the agent is looping instead of making progress."
            ),
        }
        for (tool, _args), count in seen.items()
        if count > 1
    ]


def _check_high_latency(audit_log: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    slow = [entry for entry in audit_log if entry.get("latency_ms", 0) > HIGH_LATENCY_THRESHOLD_MS]
    if not slow:
        return []
    return [{
        "issue": "High latency",
        "explanation": (
            f"{len(slow)} tool call(s) took longer than {HIGH_LATENCY_THRESHOLD_MS:.0f} ms. "
            "Consistently slow calls can point to an overloaded tool or a network issue."
        ),
    }]


def _check_failed_tool_executions(audit_log: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    failed = [entry for entry in audit_log if entry["policy_decision"] == "ALLOW" and not entry["executed"]]
    if not failed:
        return []
    return [{
        "issue": "Failed tool executions",
        "explanation": (
            f"{len(failed)} call(s) were allowed by policy but did not execute successfully. "
            "This points to a bug in the tool itself, not the guardrail."
        ),
    }]


def _check_denied_actions(audit_log: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    denied = [entry for entry in audit_log if entry["policy_decision"] == "DENY"]
    if not denied:
        return []
    return [{
        "issue": "Denied guardrail actions",
        "explanation": (
            f"{len(denied)} proposed action(s) were denied by policy. This is expected for forbidden or "
            "malformed requests, but a rising count over time is worth reviewing."
        ),
    }]


def _check_rejected_approvals(audit_log: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    rejected = [entry for entry in audit_log if entry["policy_decision"] == "REJECTED"]
    if not rejected:
        return []
    return [{
        "issue": "Rejected human approvals",
        "explanation": (
            f"{len(rejected)} sensitive action(s) were rejected by a human reviewer. Frequent rejections "
            "may mean the agent is proposing actions it should not be proposing in the first place."
        ),
    }]


def _check_excessive_steps(audit_log: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    stopped = [entry for entry in audit_log if entry["policy_decision"] == "SAFE_STOP"]
    if not stopped:
        return []
    return [{
        "issue": "Excessive agent steps / loop events",
        "explanation": (
            f"{len(stopped)} run(s) were force-stopped for exceeding the step budget or repeating an "
            "identical tool call - the signature of a runaway agent loop (failure_type: POTENTIAL_AGENT_LOOP)."
        ),
    }]


def _check_invalid_tool_requests(audit_log: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    unknown = [entry for entry in audit_log if entry["policy_decision"] == "DENY" and "Unknown tool" in entry.get("reason", "")]
    if not unknown:
        return []
    return [{
        "issue": "Invalid tool requests",
        "explanation": (
            f"{len(unknown)} request(s) named a tool the policy does not recognize at all. This can "
            "indicate a typo, a misconfigured agent, or an attempted policy bypass."
        ),
    }]


def _check_premature_answers(traces: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    findings = []
    for trace in traces:
        tool_events = [e for e in trace.get("steps", []) if e["event_type"] == "tool_call"]
        final_events = [e for e in trace.get("steps", []) if e["event_type"] == "final_result"]
        if final_events and not tool_events and trace.get("status") == "completed":
            findings.append({
                "issue": "Premature answer",
                "explanation": (
                    f"Trace {trace['trace_id']} produced a final answer without proposing any tool call - "
                    "the answer may not be grounded in real data."
                ),
            })
    return findings
