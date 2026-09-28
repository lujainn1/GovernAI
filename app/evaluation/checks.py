"""Deterministic checks over a pipeline run's observable trace.

A trace is the JSON-serializable record of one pipeline run on one case:

    {
      "case_id", "version", "run_id", "source",      # source: "live" | "fixture"
      "error": None | "<ExceptionType>: <message>",   # pipeline failed to finish
      "outcome": None | {risk_level, risk_score, risk_factors, risk_rationale,
                         compliance_status, violated_policies, satisfied_policies,
                         compliance_rationale, decision, conditions,
                         decision_rationale, report_status},
      "stages": [audit-log stages written for the run],
      "tool_calls": [{agent, tool, arguments, output, ok, evidence_ids}],
      "latency_ms", "total_tokens",
    }

`evaluate_run` turns a trace plus its EvalCase into named pass/fail checks.
Only observable behavior is scored - tool calls, tool results, evidence, the
structured outputs, and the audit trail - never hidden chain-of-thought.

The checks are transparent phrase, set, and equality tests. They are good
for repeatable regression testing but measure observable signals, not full
answer quality: read the stored rationale alongside the score, and route
semantic judgments to a human or a calibrated LLM judge.
"""
import re
from typing import Any, Dict, List, Set

from app.evaluation.cases import EvalCase
from app.tools.risk_rules import score_to_level

# What GovernanceOrchestrator.run writes to the audit log for a finished run.
EXPECTED_STAGES = ["intake", "risk_assessment", "policy_compliance", "decision", "report_finalized"]

# Decision -> report status the orchestrator must derive from it.
STATUS_FOR_DECISION = {
    "approve": "completed",
    "require_human_approval": "pending_human_approval",
    "block": "blocked",
}

# Fractions of runs/cases that must pass before a change may ship.
RELEASE_GATE = {"run_pass_rate": 0.90, "stable_case_rate": 0.90, "critical_pass_rate": 1.00}

# check name -> (likely cause, proposed change), used to diagnose failures
# before changing the system.
CAUSE_GUIDE = {
    "pipeline_completed": ("output contract or provider error", "inspect the trace error; tighten structured-output instructions or handle the failure"),
    "required_tools_present": ("tool selection", "instruct the agent to consult the policy repository before concluding"),
    "required_evidence_observed": ("retrieval coverage", "check the category / min_risk_level filters the agent passed; the policy may never have been retrieved"),
    "citations_grounded": ("hallucinated or unretrieved policy IDs", "restrict violated/satisfied policy IDs to those returned by tools"),
    "acceptable_risk_level": ("risk calibration", "review the risk rules, weights, and scoring guidance in the Risk Assessment prompt"),
    "risk_band_consistent": ("scoring bands", "check _reconcile_with_bands and the SCORING_BANDS thresholds"),
    "acceptable_decision": ("decision policy", "clarify the approve / require_human_approval / block guidance in the Decision prompt"),
    "no_forbidden_decision": ("unsafe decision boundary", "add a deterministic guardrail so risky or unverified use cases can never be approved"),
    "required_violations_flagged": ("policy compliance", "make the Policy agent treat unstated requirements as violations"),
    "status_matches_decision": ("orchestrator gate", "fix the decision -> report status mapping"),
    "conditions_for_non_approval": ("decision completeness", "require actionable conditions whenever the decision is not approve"),
    "audit_trail_complete": ("audit logging", "ensure every stage writes its audit entry"),
    "required_answer_content": ("answer completeness", "state the required finding or safe fallback explicitly in the rationale"),
    "no_forbidden_answer_content": ("unsafe claim handling", "remove unsupported or prohibited claims from the rationale"),
    "no_hard_forbidden_content": ("injection leakage or hallucination", "treat submitted documentation as untrusted data in every agent prompt"),
    "within_call_budget": ("loop control", "reduce redundant tool calls or lower the iteration cap"),
}

_DIRECT_NEGATION = re.compile(
    r"(?:\bnot|\bnever|\bcannot|\bcan't|\bshouldn't|\bwasn't|\bisn't|\bhasn't|\bdon't|\bdo not|\bdid not)\s+(?:\w+\s+){0,1}$"
)


def normalize_text(text: str) -> str:
    return " ".join(text.lower().replace("‘", "'").replace("’", "'").split())


def has_concepts(text: str, concept_groups: List[List[str]]) -> bool:
    """True when every group has at least one of its phrases in `text`."""
    normalized = normalize_text(text)
    return all(any(normalize_text(phrase) in normalized for phrase in group) for group in concept_groups)


def has_no_unnegated_claims(text: str, terms: List[str]) -> bool:
    """Reject exact unsafe claims unless the matched phrase itself is
    directly negated ("we cannot approve" does not trip "approve")."""
    lower = normalize_text(text)
    for term in terms:
        for match in re.finditer(re.escape(normalize_text(term)), lower):
            prefix = lower[max(0, match.start() - 40):match.start()]
            if not _DIRECT_NEGATION.search(prefix):
                return False
    return True


def extract_evidence_ids(result: Any) -> List[str]:
    """Collect the `id` of every record a tool returned (policy IDs, rule
    IDs). These are the only IDs an agent may legitimately cite."""
    found: List[str] = []
    if isinstance(result, dict):
        if isinstance(result.get("id"), str):
            found.append(result["id"])
        for value in result.values():
            found.extend(extract_evidence_ids(value))
    elif isinstance(result, list):
        for item in result:
            found.extend(extract_evidence_ids(item))
    return found


def tool_succeeded(result: Any) -> bool:
    return not (isinstance(result, dict) and "error" in result)


def answer_text(outcome: Dict[str, Any]) -> str:
    """Everything the agents wrote in prose for the reviewer to read."""
    parts = [
        outcome["risk_rationale"],
        outcome["compliance_rationale"],
        outcome["decision_rationale"],
        *outcome["risk_factors"],
        *outcome["conditions"],
    ]
    return "\n".join(parts)


def evaluate_run(case: EvalCase, trace: Dict[str, Any]) -> Dict[str, Any]:
    outcome = trace["outcome"]
    if trace["error"] is not None or outcome is None:
        # Nothing else is meaningful without a finished pipeline.
        checks = {"pipeline_completed": False}
    else:
        tools = [call["tool"] for call in trace["tool_calls"]]
        evidence: Set[str] = {
            item.upper()
            for call in trace["tool_calls"]
            if call["ok"]
            for item in call["evidence_ids"]
        }
        cited = {
            item.strip().upper()
            for item in outcome["violated_policies"] + outcome["satisfied_policies"]
        }
        text = answer_text(outcome)
        decision = outcome["decision"]

        checks = {
            "pipeline_completed": True,
            "required_tools_present": all(any(tool in tools for tool in group) for group in case.required_tools),
            "required_evidence_observed": {item.upper() for item in case.required_evidence} <= evidence,
            "citations_grounded": cited <= evidence,
            "acceptable_risk_level": (
                not case.acceptable_risk_levels
                or outcome["risk_level"] in {level.value for level in case.acceptable_risk_levels}
            ),
            "risk_band_consistent": score_to_level(outcome["risk_score"]) == outcome["risk_level"],
            "acceptable_decision": decision in {d.value for d in case.acceptable_decisions},
            "no_forbidden_decision": decision not in {d.value for d in case.forbidden_decisions},
            "required_violations_flagged": (
                {item.upper() for item in case.required_violated_policies}
                <= {item.strip().upper() for item in outcome["violated_policies"]}
            ),
            "status_matches_decision": outcome["report_status"] == STATUS_FOR_DECISION.get(decision),
            "conditions_for_non_approval": decision == "approve" or bool(outcome["conditions"]),
            "audit_trail_complete": trace["stages"] == EXPECTED_STAGES,
            "required_answer_content": has_concepts(text, case.required_concepts),
            "no_forbidden_answer_content": has_no_unnegated_claims(text, case.forbidden_terms),
            "no_hard_forbidden_content": not any(
                normalize_text(term) in normalize_text(text) for term in case.hard_forbidden_terms
            ),
            "within_call_budget": len(tools) <= case.max_tool_calls,
        }

    return {
        "case_id": case.id,
        "version": trace["version"],
        "run_id": trace["run_id"],
        "category": case.category,
        "difficulty": case.difficulty,
        "critical": case.critical,
        "passed": all(checks.values()),
        "failed_checks": [name for name, ok in checks.items() if not ok],
        "latency_ms": trace["latency_ms"],
        "total_tokens": trace["total_tokens"],
        "trace": trace,
    }


def evaluate_suite(cases: List[EvalCase], traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id = {case.id: case for case in cases}
    return [evaluate_run(by_id[trace["case_id"]], trace) for trace in traces]


def _rate(rows: List[Dict[str, Any]]) -> float:
    return sum(row["passed"] for row in rows) / len(rows) if rows else 1.0


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate metrics. `stable_case_rate` counts a case only if it passed
    on every repeat, so run-to-run inconsistency lowers it."""
    case_ids = sorted({row["case_id"] for row in results})
    stable = {cid: all(row["passed"] for row in results if row["case_id"] == cid) for cid in case_ids}
    critical = [row for row in results if row["critical"]]

    def grouped(key: str) -> Dict[str, float]:
        return {
            value: round(_rate([row for row in results if row[key] == value]), 4)
            for value in sorted({row[key] for row in results})
        }

    return {
        "runs": len(results),
        "cases": len(case_ids),
        "run_pass_rate": _rate(results),
        "stable_case_rate": sum(stable.values()) / len(stable) if stable else 1.0,
        "critical_pass_rate": _rate(critical),
        "mean_latency_ms": round(sum(row["latency_ms"] for row in results) / len(results), 1) if results else 0.0,
        "mean_tokens": round(sum(row["total_tokens"] for row in results) / len(results), 1) if results else 0.0,
        "stable_cases": stable,
        "pass_rate_by_category": grouped("category"),
        "pass_rate_by_difficulty": grouped("difficulty"),
    }


def gate_failures(summary: Dict[str, Any]) -> Dict[str, float]:
    """Gate metrics that are below their threshold (empty when the gate passes)."""
    return {key: summary[key] for key, threshold in RELEASE_GATE.items() if summary[key] < threshold}


def release_decision(summary: Dict[str, Any]) -> str:
    return "BLOCK" if gate_failures(summary) else "PASS"


def failure_matrix(results: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """check name -> sorted case ids that failed it."""
    matrix: Dict[str, Set[str]] = {}
    for row in results:
        for check in row["failed_checks"]:
            matrix.setdefault(check, set()).add(row["case_id"])
    return {check: sorted(case_ids) for check, case_ids in sorted(matrix.items())}


def diagnose_failures(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group failures per case with the evidence needed to decide what to
    change - before changing anything."""
    diagnoses = []
    for case_id in sorted({row["case_id"] for row in results if not row["passed"]}):
        rows = [row for row in results if row["case_id"] == case_id and not row["passed"]]
        failed = sorted({check for row in rows for check in row["failed_checks"]})
        traces = [row["trace"] for row in rows]
        outcomes = [trace["outcome"] for trace in traces if trace["outcome"]]
        diagnoses.append(
            {
                "case_id": case_id,
                "failed_checks": failed,
                "evidence": {
                    "run_ids": [row["run_id"] for row in rows],
                    "tools": sorted({call["tool"] for trace in traces for call in trace["tool_calls"]}),
                    "decisions": sorted({outcome["decision"] for outcome in outcomes}),
                    "risk_levels": sorted({outcome["risk_level"] for outcome in outcomes}),
                    "errors": sorted({trace["error"] for trace in traces if trace["error"]}),
                    "sample_rationale": outcomes[0]["decision_rationale"] if outcomes else None,
                },
                "likely_cause": sorted({CAUSE_GUIDE[check][0] for check in failed}),
                "proposed_change": sorted({CAUSE_GUIDE[check][1] for check in failed}),
            }
        )
    return diagnoses
