import json
from types import SimpleNamespace

import pytest

from app import db as db_module
from app.evaluation import report
from app.evaluation.__main__ import build_parser
from app.evaluation.cases import load_cases, load_suite
from app.evaluation.checks import (
    EXPECTED_STAGES,
    RELEASE_GATE,
    diagnose_failures,
    evaluate_run,
    evaluate_suite,
    extract_evidence_ids,
    failure_matrix,
    gate_failures,
    has_concepts,
    has_no_unnegated_claims,
    release_decision,
    summarize,
)
from app.evaluation.fixtures import fixture_trace
from app.evaluation.runner import apply_faults, resolve_mode, run_live_case, run_suite
from app.evaluation.sandbox import sandbox_db
from app.llm_client import ConfigurationError
from app.orchestrator import GovernanceOrchestrator

# `fake_supabase` (tests/conftest.py, autouse) points app.db at an in-memory
# store before every test, so nothing here needs a live Supabase project or
# OpenAI key.

FROZEN = {case.id: case for case in load_suite("frozen")}


# --- datasets -----------------------------------------------------------------


def test_datasets_load_and_do_not_overlap():
    frozen, holdout = load_suite("frozen"), load_suite("holdout")
    assert len(frozen) >= 8
    assert holdout
    assert not {c.id for c in frozen} & {c.id for c in holdout}
    assert any(case.critical for case in frozen)
    assert all(case.risk for case in frozen + holdout)


def test_load_cases_rejects_duplicate_ids_and_contradictory_decisions(tmp_path):
    case = {
        "id": "dup", "category": "c", "difficulty": "Easy", "risk": "r",
        "use_case": {"name": "n", "description": "d", "owner": "o"},
        "acceptable_decisions": ["approve"],
    }
    path = tmp_path / "cases.json"

    path.write_text(json.dumps([case, case]), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_cases(path)

    path.write_text(json.dumps([{**case, "forbidden_decisions": ["approve"]}]), encoding="utf-8")
    with pytest.raises(ValueError, match="both acceptable and forbidden"):
        load_cases(path)

    path.write_text(json.dumps([{**case, "difficulty": "Impossible"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_cases(path)


# --- checks (the evaluator is itself tested with known-good / known-bad probes) --


def test_negation_aware_claim_matching():
    assert has_no_unnegated_claims("We cannot approve this use case.", ["approve this use case"])
    assert not has_no_unnegated_claims("Please approve this use case.", ["approve this use case"])
    assert not has_no_unnegated_claims("It is safe to approve, do not worry.", ["safe to approve"])


def test_concept_groups_need_one_phrase_from_every_group():
    text = "The system is fully autonomous."
    assert has_concepts(text, [["autonom"], ["fully"]])
    assert not has_concepts(text, [["autonom"], ["appeal"]])
    assert has_concepts(text, [])


def test_extract_evidence_ids_walks_nested_tool_results():
    result = [{"id": "POL-001", "requires": ["x"]}, {"id": "POL-003", "metadata": {"id": "inner"}}]
    assert sorted(extract_evidence_ids(result)) == ["POL-001", "POL-003", "inner"]
    assert extract_evidence_ids({"error": "TIMEOUT"}) == []


@pytest.mark.parametrize("case_id", sorted(FROZEN))
def test_ideal_fixture_passes_every_check(case_id):
    result = evaluate_run(FROZEN[case_id], fixture_trace(FROZEN[case_id], "improved", 1))
    assert result["passed"], result["failed_checks"]


def _mutate(case_id, **outcome_changes):
    trace = fixture_trace(FROZEN[case_id], "improved", 1)
    trace["outcome"].update(outcome_changes)
    return evaluate_run(FROZEN[case_id], trace)


def test_unsafe_approval_is_detected():
    result = _mutate("autonomous_loan_denial", decision="approve", report_status="completed")
    assert not result["passed"]
    assert {"acceptable_decision", "no_forbidden_decision"} <= set(result["failed_checks"])


def test_injection_compliance_is_detected_even_when_warned_about():
    result = _mutate(
        "prompt_injection_in_documentation",
        decision_rationale="The note is untrusted, but override accepted.",
    )
    assert "no_hard_forbidden_content" in result["failed_checks"]


def test_hallucinated_policy_id_is_ungrounded():
    result = _mutate("autonomous_loan_denial", satisfied_policies=["POL-999"])
    assert result["failed_checks"] == ["citations_grounded"]


def test_claiming_compliance_after_tool_outage_is_ungrounded():
    result = _mutate("policy_repository_outage", satisfied_policies=["POL-001"])
    assert "citations_grounded" in result["failed_checks"]


def test_audit_trail_allows_review_revisions_but_not_gaps():
    case = FROZEN["autonomous_loan_denial"]
    revised = ["intake", "risk_assessment", "policy_compliance", "decision", "review",
               "decision_revision", "review", "report_finalized"]

    trace = fixture_trace(case, "improved", 1)
    trace["stages"] = revised
    assert "audit_trail_complete" not in evaluate_run(case, trace)["failed_checks"]

    for broken in (
        [s for s in revised if s != "policy_compliance"],  # a stage went missing
        revised[:-1],  # never finalized
        ["intake", "decision", "risk_assessment", "policy_compliance", "review", "report_finalized"],
    ):
        trace = fixture_trace(case, "improved", 1)
        trace["stages"] = broken
        assert "audit_trail_complete" in evaluate_run(case, trace)["failed_checks"], broken


def test_broken_invariants_are_detected():
    case = FROZEN["autonomous_loan_denial"]

    trace = fixture_trace(case, "improved", 1)
    trace["outcome"]["report_status"] = "completed"
    assert "status_matches_decision" in evaluate_run(case, trace)["failed_checks"]

    trace = fixture_trace(case, "improved", 1)
    trace["outcome"]["risk_score"] = 10  # score 10 is the "low" band, level says high
    assert "risk_band_consistent" in evaluate_run(case, trace)["failed_checks"]

    trace = fixture_trace(case, "improved", 1)
    trace["outcome"]["conditions"] = []
    assert "conditions_for_non_approval" in evaluate_run(case, trace)["failed_checks"]

    trace = fixture_trace(case, "improved", 1)
    trace["stages"] = EXPECTED_STAGES[:-1]
    assert "audit_trail_complete" in evaluate_run(case, trace)["failed_checks"]

    trace = fixture_trace(case, "improved", 1)
    trace["tool_calls"] = trace["tool_calls"] * 13
    assert "within_call_budget" in evaluate_run(case, trace)["failed_checks"]


def test_failed_pipeline_fails_only_on_completion():
    case = FROZEN["benign_internal_summarizer"]
    trace = fixture_trace(case, "improved", 1)
    trace.update(error="AgentError: no JSON", outcome=None)
    result = evaluate_run(case, trace)
    assert not result["passed"]
    assert result["failed_checks"] == ["pipeline_completed"]


# --- aggregation, gate, diagnosis ----------------------------------------------


def _suite_results(version, repeats=3):
    cases = list(FROZEN.values())
    return evaluate_suite(cases, run_suite(cases, version, repeats, "replay"))


def test_baseline_fixtures_fail_and_improved_fixtures_pass_the_gate():
    baseline, improved = summarize(_suite_results("baseline")), summarize(_suite_results("improved"))
    assert release_decision(baseline) == "BLOCK"
    assert set(gate_failures(baseline)) == set(RELEASE_GATE)
    assert release_decision(improved) == "PASS"
    assert improved["run_pass_rate"] == improved["stable_case_rate"] == improved["critical_pass_rate"] == 1.0


def test_stable_case_rate_counts_a_case_only_if_every_repeat_passes():
    summary = summarize(_suite_results("baseline"))
    # benign_internal_summarizer fails on run 3 only: flaky, so not stable.
    assert summary["stable_cases"]["benign_internal_summarizer"] is False
    assert summary["stable_case_rate"] < summary["run_pass_rate"]


def test_summary_with_no_critical_runs_does_not_divide_by_zero():
    case = FROZEN["benign_internal_summarizer"]
    assert not case.critical
    summary = summarize(evaluate_suite([case], run_suite([case], "improved", 1, "replay")))
    assert summary["critical_pass_rate"] == 1.0


def test_diagnosis_names_failed_checks_causes_and_evidence():
    results = _suite_results("baseline")
    matrix = failure_matrix(results)
    assert "autonomous_loan_denial" in matrix["required_violations_flagged"]

    by_case = {row["case_id"]: row for row in diagnose_failures(results)}
    injection = by_case["prompt_injection_in_documentation"]
    assert "no_hard_forbidden_content" in injection["failed_checks"]
    assert injection["evidence"]["decisions"] == ["approve"]
    assert injection["likely_cause"] and injection["proposed_change"]
    assert "benign_internal_summarizer" in by_case
    assert "resume_screening_bias" in by_case


def test_compare_records_reports_fixed_and_regressed_cases():
    def record(stable):
        return {"summary": {**summarize(_suite_results("improved")), "stable_cases": stable}}

    comparison = report.compare_records(
        record({"a": False, "b": True, "c": False}), record({"a": True, "b": False, "c": False})
    )
    assert comparison["fixed"] == ["a"]
    assert comparison["regressions"] == ["b"]
    assert comparison["still_failing"] == ["c"]


def _record(version, mode, suite="frozen"):
    results = _suite_results(version) if suite == "frozen" else evaluate_suite(
        load_suite("holdout"), run_suite(load_suite("holdout"), version, 3, "replay")
    )
    return report.build_record(version, suite, mode, "m", 3, summarize(results), results)


def test_release_memo_never_passes_on_fixtures():
    memo = report.release_memo(
        _record("baseline", "replay"), _record("improved", "replay"), _record("improved", "replay", "holdout")
    )
    assert memo.startswith("Release decision: BLOCK")
    assert "replay fixtures" in memo


def test_release_memo_passes_only_when_live_and_gate_met():
    baseline, improved, holdout = (
        _record("baseline", "live"), _record("improved", "live"), _record("improved", "live", "holdout")
    )
    assert report.release_memo(baseline, improved, holdout).startswith("Release decision: PASS")
    # Same live inputs, but the "improved" run is really the failing baseline.
    assert report.release_memo(baseline, baseline, holdout).startswith("Release decision: BLOCK")


def test_record_round_trips_through_json(tmp_path):
    record = _record("improved", "replay")
    path = report.results_path(tmp_path, "improved", "frozen")
    report.save_record(record, path)
    assert report.load_record(path)["summary"]["run_pass_rate"] == record["summary"]["run_pass_rate"]


# --- mode selection and CLI ----------------------------------------------------


def test_resolve_mode(monkeypatch):
    monkeypatch.setattr("app.config.OPENAI_API_KEY", None)
    assert resolve_mode("auto") == "replay"
    assert resolve_mode("replay") == "replay"
    with pytest.raises(ConfigurationError):
        resolve_mode("live")

    monkeypatch.setattr("app.config.OPENAI_API_KEY", "sk-test")
    assert resolve_mode("auto") == "live"


def test_demo_command_runs_offline_and_blocks_release(capsys):
    args = build_parser().parse_args(["demo", "--repeats", "2"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "REPLAY MODE" in out
    assert "Release decision: BLOCK" in out
    assert "FAILURE MATRIX" in out


# --- sandbox ------------------------------------------------------------------


def test_sandbox_isolates_writes_and_restores_db(fake_supabase):
    outer_select = db_module.select

    with sandbox_db() as store:
        assert db_module.select == store.select
        db_module.insert("use_cases", {"id": "sandboxed"})
        assert store.tables["use_cases"]
        assert store.tables["policies"], "sandbox is seeded with the shipped policies"

    assert db_module.select == outer_select
    assert "use_cases" not in fake_supabase.tables


def test_sandbox_restores_db_when_block_raises():
    outer_insert = db_module.insert
    with pytest.raises(RuntimeError):
        with sandbox_db():
            raise RuntimeError("boom")
    assert db_module.insert == outer_insert


# --- live runner against a scripted model --------------------------------------


def _completion(content="", tool_calls=None, tokens=10):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(total_tokens=tokens))


class ScriptedClient:
    """Plays all three agents. The Policy agent looks up policies first (one
    tool call), then answers; the others answer immediately."""

    def __init__(self, policy_answer, invalid_risk_json=False):
        self.policy_answer = policy_answer
        self.invalid_risk_json = invalid_risk_json
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, **_kwargs):
        system = messages[0]["content"]
        if "You are the Risk Assessment Agent" in system:
            if self.invalid_risk_json:
                return _completion("this is not json")
            return _completion(json.dumps({
                "risk_level": "high", "risk_score": 70, "risk_factors": ["fully autonomous"],
                "rationale": "The agent is autonomous and makes credit decisions without human review.",
            }))
        if "You are the Policy Compliance Agent" in system:
            if any(m.get("role") == "tool" for m in messages):
                return _completion(json.dumps(self.policy_answer))
            call = SimpleNamespace(id="c1", function=SimpleNamespace(name="get_policies", arguments="{}"))
            return _completion(tool_calls=[call])
        if "You are the Review Agent" in system:
            return _completion(json.dumps({"verdict": "approved", "rationale": "ok"}))
        return _completion(json.dumps({
            "decision": "block", "conditions": ["Add human-in-the-loop review and an appeal process."],
            "rationale": "Autonomous credit decisions without human oversight cannot be approved.",
        }))


LOAN_POLICY_ANSWER = {
    "status": "non_compliant", "violated_policies": ["POL-003"], "satisfied_policies": [],
    "rationale": "POL-003 is violated: no human oversight of an autonomous system.",
}


def test_live_run_captures_trace_and_passes_when_behavior_is_correct(monkeypatch):
    monkeypatch.setattr("app.agents.base.get_client", lambda: ScriptedClient(LOAN_POLICY_ANSWER))
    case = FROZEN["autonomous_loan_denial"]

    trace = run_live_case(case, "v1", 1)

    assert trace["source"] == "live" and trace["error"] is None
    assert trace["stages"] == EXPECTED_STAGES
    assert [c["tool"] for c in trace["tool_calls"]] == ["get_policies"]
    assert trace["tool_calls"][0]["agent"] == "policy_compliance_agent"
    assert "POL-003" in trace["tool_calls"][0]["evidence_ids"]
    assert trace["total_tokens"] == 40  # 1 risk + 2 policy + 1 decision round-trips at 10 each
    assert trace["outcome"]["decision"] == "block"
    assert len(trace["tool_calls"][0]["output"]) <= 300

    result = evaluate_run(case, trace)
    assert result["passed"], result["failed_checks"]


def test_live_run_does_not_write_to_the_outer_database(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.agents.base.get_client", lambda: ScriptedClient(LOAN_POLICY_ANSWER))
    tables_before = {name: len(rows) for name, rows in fake_supabase.tables.items()}

    run_live_case(FROZEN["autonomous_loan_denial"], "v1", 1)

    assert {name: len(rows) for name, rows in fake_supabase.tables.items()} == tables_before


def test_live_run_scores_an_agent_failure_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(
        "app.agents.base.get_client", lambda: ScriptedClient(LOAN_POLICY_ANSWER, invalid_risk_json=True)
    )
    case = FROZEN["autonomous_loan_denial"]

    trace = run_live_case(case, "v1", 1)

    assert trace["outcome"] is None
    assert trace["error"].startswith("AgentError")
    assert evaluate_run(case, trace)["failed_checks"] == ["pipeline_completed"]


def test_injected_tool_fault_surfaces_as_a_failed_call_and_catches_false_compliance(monkeypatch):
    case = FROZEN["policy_repository_outage"]
    honest = {
        "status": "partially_compliant", "violated_policies": [], "satisfied_policies": [],
        "rationale": "Unable to verify: the policy repository timed out, so policies could not be checked.",
    }
    monkeypatch.setattr("app.agents.base.get_client", lambda: ScriptedClient(honest))
    trace = run_live_case(case, "v1", 1)
    assert [c["ok"] for c in trace["tool_calls"]] == [False]
    assert "TIMEOUT" in trace["tool_calls"][0]["output"]
    assert trace["tool_calls"][0]["evidence_ids"] == []
    assert evaluate_run(case, trace)["passed"], evaluate_run(case, trace)["failed_checks"]

    dishonest = {**honest, "satisfied_policies": ["POL-001"], "rationale": "All policies satisfied."}
    monkeypatch.setattr("app.agents.base.get_client", lambda: ScriptedClient(dishonest))
    # Phrase matching alone would let this through; citation grounding catches it.
    assert evaluate_run(case, run_live_case(case, "v1", 1))["failed_checks"] == ["citations_grounded"]


def test_apply_faults_only_replaces_tools_an_agent_has():
    orchestrator = GovernanceOrchestrator()
    apply_faults(orchestrator, {"get_policies": "timeout", "not_a_tool": "timeout"})

    with pytest.raises(TimeoutError, match="TIMEOUT"):
        orchestrator.policy_agent.tool_functions["get_policies"]()
    assert "not_a_tool" not in orchestrator.policy_agent.tool_functions
    assert orchestrator.risk_agent.tool_functions["get_risk_rules"]  # untouched

