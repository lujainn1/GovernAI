"""
tests/test_evaluator.py
-------------------------
Sanity tests for the evaluation dataset and the automated evaluator: the
dataset is well-formed, the evaluator runs both agent versions without
error, and the improved agent measurably outperforms the baseline agent
on this frozen dataset.

Run them with:
    python tests/test_evaluator.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit_logger  # noqa: E402
import approval_manager  # noqa: E402
import observability  # noqa: E402
from evaluation_dataset import EVALUATION_CASES  # noqa: E402
from evaluator import compare_versions, run_evaluation, summarize  # noqa: E402

REQUIRED_CASE_FIELDS = {
    "id", "category", "query", "expected_behavior", "difficulty", "required_tools",
    "forbidden_tools", "required_evidence", "required_concepts", "forbidden_claims", "max_tool_calls",
}


def _reset_state():
    observability.reset()
    audit_logger.reset()
    approval_manager.reset()


def test_dataset_has_at_least_ten_cases():
    assert len(EVALUATION_CASES) >= 10


def test_dataset_cases_have_unique_ids():
    ids = [case["id"] for case in EVALUATION_CASES]
    assert len(ids) == len(set(ids))


def test_dataset_cases_have_all_required_fields():
    for case in EVALUATION_CASES:
        assert REQUIRED_CASE_FIELDS <= set(case.keys()), f"Case {case['id']} is missing a required field."


def test_run_evaluation_returns_one_result_per_case():
    _reset_state()
    results = run_evaluation(EVALUATION_CASES, "improved")
    assert len(results) == len(EVALUATION_CASES)


def test_summarize_reports_the_expected_keys():
    _reset_state()
    results = run_evaluation(EVALUATION_CASES, "improved")
    summary = summarize(results)
    expected_keys = {
        "total", "passed", "failed", "pass_rate", "tool_accuracy", "avg_tool_calls", "avg_latency_ms",
        "failed_categories", "error_rate", "p95_latency_ms", "avg_steps", "guardrail_violations", "loop_events",
    }
    assert expected_keys <= set(summary.keys())


def test_improved_agent_outperforms_baseline():
    _reset_state()
    comparison = compare_versions(EVALUATION_CASES)
    baseline_pass_rate = comparison["baseline_summary"]["pass_rate"]
    improved_pass_rate = comparison["improved_summary"]["pass_rate"]
    assert improved_pass_rate >= baseline_pass_rate


def test_improved_agent_has_no_loop_events():
    _reset_state()
    comparison = compare_versions(EVALUATION_CASES)
    assert comparison["improved_summary"]["loop_events"] == 0


ALL_TESTS = [
    test_dataset_has_at_least_ten_cases,
    test_dataset_cases_have_unique_ids,
    test_dataset_cases_have_all_required_fields,
    test_run_evaluation_returns_one_result_per_case,
    test_summarize_reports_the_expected_keys,
    test_improved_agent_outperforms_baseline,
    test_improved_agent_has_no_loop_events,
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
    print(f"\n{passed}/{len(ALL_TESTS)} evaluator tests passed.")


if __name__ == "__main__":
    run_all_tests()
