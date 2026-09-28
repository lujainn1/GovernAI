"""Run evaluation cases through the pipeline and capture observable traces.

Two modes produce traces with the same schema (see app.evaluation.checks):

- live:   the real Risk / Policy / Decision agents call the OpenAI API and
          their tools, inside an in-memory sandbox (app.evaluation.sandbox),
          so nothing is written to the production database. This is the
          only mode whose results can support a release decision.
- replay: deterministic fixtures (app.evaluation.fixtures) exercise the
          evaluator and reports without an API key or cost. They are
          instructional, not empirical model results.
"""
import json
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from app import config
from app.evaluation.cases import EvalCase
from app.evaluation.checks import extract_evidence_ids, tool_succeeded
from app.evaluation.fixtures import fixture_trace
from app.evaluation.sandbox import sandbox_db
from app.llm_client import ConfigurationError
from app.models import GovernanceReport
from app.orchestrator import GovernanceOrchestrator
from app.tools.audit_log import get_audit_log

_OUTPUT_PREVIEW_CHARS = 300


def resolve_mode(requested: str) -> str:
    """'auto' means live when an OpenAI key is configured, else replay."""
    if requested == "auto":
        return "live" if config.OPENAI_API_KEY else "replay"
    if requested == "live" and not config.OPENAI_API_KEY:
        raise ConfigurationError("live mode needs OPENAI_API_KEY; use --mode replay for fixtures")
    return requested


def _agents(orchestrator: GovernanceOrchestrator) -> List[Any]:
    return [orchestrator.risk_agent, orchestrator.policy_agent, orchestrator.decision_agent]


def _failing_tool(name: str, fault: str) -> Callable[..., Any]:
    def fail(**_kwargs: Any) -> Any:
        raise TimeoutError(f"{fault.upper()}: {name} did not respond")

    return fail


def apply_faults(orchestrator: GovernanceOrchestrator, faults: Dict[str, str]) -> None:
    """Make the named tools fail whenever an agent's model calls them. The
    orchestrator's own deterministic pre-analysis calls are not affected."""
    for agent in _agents(orchestrator):
        for name, fault in faults.items():
            if name in agent.tool_functions:
                agent.tool_functions[name] = _failing_tool(name, fault)


def outcome_from_report(report: GovernanceReport) -> Dict[str, Any]:
    return {
        "risk_level": report.risk_assessment.risk_level.value,
        "risk_score": report.risk_assessment.risk_score,
        "risk_factors": report.risk_assessment.risk_factors,
        "risk_rationale": report.risk_assessment.rationale,
        "compliance_status": report.policy_compliance.status.value,
        "violated_policies": report.policy_compliance.violated_policies,
        "satisfied_policies": report.policy_compliance.satisfied_policies,
        "compliance_rationale": report.policy_compliance.rationale,
        "decision": report.decision.decision.value,
        "conditions": report.decision.conditions,
        "decision_rationale": report.decision.rationale,
        "report_status": report.status.value,
    }


def _collect_tool_calls(orchestrator: GovernanceOrchestrator) -> List[Dict[str, Any]]:
    calls = []
    for agent in _agents(orchestrator):
        for call in agent.last_trace["tool_calls"]:
            ok = tool_succeeded(call["result"])
            calls.append(
                {
                    "agent": agent.name,
                    "tool": call["tool"],
                    "arguments": call["arguments"],
                    "output": json.dumps(call["result"], default=str)[:_OUTPUT_PREVIEW_CHARS],
                    "ok": ok,
                    "evidence_ids": extract_evidence_ids(call["result"]) if ok else [],
                }
            )
    return calls


def run_live_case(case: EvalCase, version: str, run_id: int, model: Optional[str] = None) -> Dict[str, Any]:
    # A fresh id per run keeps repeats independent of each other.
    use_case = case.use_case.model_copy(update={"id": str(uuid.uuid4())})
    outcome, error, stages = None, None, []
    started = time.perf_counter()

    with sandbox_db():
        orchestrator = GovernanceOrchestrator(model=model)
        apply_faults(orchestrator, case.faults)
        try:
            outcome = outcome_from_report(orchestrator.run(use_case))
        except ConfigurationError:
            raise
        except Exception as exc:  # an agent failing is a result to score, not a crash
            error = f"{type(exc).__name__}: {exc}"
        stages = [entry["stage"] for entry in get_audit_log(use_case.id)]

    return {
        "case_id": case.id,
        "version": version,
        "run_id": run_id,
        "source": "live",
        "error": error,
        "outcome": outcome,
        "stages": stages,
        "tool_calls": _collect_tool_calls(orchestrator),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "total_tokens": sum(agent.last_trace["total_tokens"] for agent in _agents(orchestrator)),
    }


def run_case(
    case: EvalCase, version: str, run_id: int, mode: str, model: Optional[str] = None
) -> Dict[str, Any]:
    if mode == "live":
        return run_live_case(case, version, run_id, model)
    return fixture_trace(case, version, run_id)


def run_suite(
    cases: List[EvalCase],
    version: str,
    repeats: int,
    mode: str,
    model: Optional[str] = None,
    on_run: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> List[Dict[str, Any]]:
    """Every case is run `repeats` times so run-to-run inconsistency shows up
    in the stable-case rate."""
    traces = []
    for run_id in range(1, repeats + 1):
        for case in cases:
            trace = run_case(case, version, run_id, mode, model)
            traces.append(trace)
            if on_run:
                on_run(trace)
    return traces
