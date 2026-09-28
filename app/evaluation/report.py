"""Saving, printing, and comparing evaluation runs, and the release memo."""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from app import config
from app.evaluation.checks import (
    RELEASE_GATE,
    diagnose_failures,
    failure_matrix,
    gate_failures,
    release_decision,
)

RESULTS_DIR = config.DATA_DIR / "evaluation" / "results"
_METRICS = ["run_pass_rate", "stable_case_rate", "critical_pass_rate", "mean_latency_ms", "mean_tokens"]
_WIDTH = 78

FIXTURE_BANNER = (
    "REPLAY MODE: results come from deterministic fixtures, not the model. They test the "
    "evaluator and cannot authorize a release. Set OPENAI_API_KEY and use --mode live."
)


def results_path(directory: Path, version: str, suite: str) -> Path:
    return directory / f"agent_eval_{version}_{suite}.json"


def build_record(
    version: str, suite: str, mode: str, model: str, repeats: int,
    summary: Dict[str, Any], results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "meta": {
            "version": version,
            "suite": suite,
            "mode": mode,
            "model": model if mode == "live" else "deterministic fixtures",
            "repeats": repeats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "summary": summary,
        "results": results,
    }


def save_record(record: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def load_record(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _pct(value: float) -> str:
    return f"{value * 100:5.1f}%"


def print_run_report(record: Dict[str, Any]) -> None:
    meta, summary, results = record["meta"], record["summary"], record["results"]
    print("\n" + "=" * _WIDTH)
    print(f"GovernAI Agent Evaluation: {meta['version']} / {meta['suite']}".center(_WIDTH))
    print("=" * _WIDTH)
    print(f"Mode: {meta['mode']} | Model: {meta['model']} | Cases: {summary['cases']} | Repeats: {meta['repeats']}")
    if meta["mode"] != "live":
        print(f"\n{FIXTURE_BANNER}")

    print("\nMETRICS")
    print("-" * _WIDTH)
    for key in ["run_pass_rate", "stable_case_rate", "critical_pass_rate"]:
        print(f"{key:<22}{_pct(summary[key])}   (gate >= {RELEASE_GATE[key]:.0%})")
    print(f"{'mean_latency_ms':<22}{summary['mean_latency_ms']}")
    print(f"{'mean_tokens':<22}{summary['mean_tokens']}")

    print("\nPASS RATE BY CATEGORY")
    for name, rate in summary["pass_rate_by_category"].items():
        print(f"  {name:<28}{_pct(rate)}")
    print("PASS RATE BY DIFFICULTY")
    for name, rate in summary["pass_rate_by_difficulty"].items():
        print(f"  {name:<28}{_pct(rate)}")

    failures = [row for row in results if not row["passed"]]
    if failures:
        print("\nFAILED RUNS")
        for row in failures:
            flag = " [CRITICAL]" if row["critical"] else ""
            print(f"  {row['case_id']} / run {row['run_id']}{flag}: {row['failed_checks']}")

    print(f"\nGATE: {release_decision(summary)}", end="")
    missed = gate_failures(summary)
    print(f"  (below threshold: {', '.join(missed)})" if missed else "")
    print("=" * _WIDTH)


def print_diagnosis(record: Dict[str, Any]) -> None:
    results = record["results"]
    matrix = failure_matrix(results)
    if not matrix:
        print("\nNo failures to diagnose.")
        return
    print("\nFAILURE MATRIX (check -> cases)")
    for check, case_ids in matrix.items():
        print(f"  {check}: {case_ids}")
    print("\nDIAGNOSIS (review the traces before changing anything)")
    for row in diagnose_failures(results):
        print(f"  {row['case_id']}")
        print(f"    failed checks : {row['failed_checks']}")
        print(f"    likely cause  : {row['likely_cause']}")
        print(f"    proposed      : {row['proposed_change']}")
        print(f"    evidence      : {json.dumps(row['evidence'], ensure_ascii=False)}")


def compare_records(baseline: Dict[str, Any], improved: Dict[str, Any]) -> Dict[str, Any]:
    """Metric deltas plus per-case movement, so a change that fixes some
    cases while breaking others is visible."""
    before, after = baseline["summary"]["stable_cases"], improved["summary"]["stable_cases"]
    shared = sorted(set(before) & set(after))
    return {
        "metrics": {
            key: {"baseline": baseline["summary"][key], "improved": improved["summary"][key]}
            for key in _METRICS
        },
        "regressions": [cid for cid in shared if before[cid] and not after[cid]],
        "fixed": [cid for cid in shared if not before[cid] and after[cid]],
        "still_failing": [cid for cid in shared if not before[cid] and not after[cid]],
    }


def print_comparison(comparison: Dict[str, Any]) -> None:
    print("\nCOMPARISON (same frozen suite)")
    print("-" * _WIDTH)
    for key, values in comparison["metrics"].items():
        before, after = (round(values[side], 3) for side in ("baseline", "improved"))
        print(f"{key:<22}baseline={before} | improved={after}")
    print(f"Fixed cases      : {comparison['fixed'] or 'none'}")
    print(f"Regressions      : {comparison['regressions'] or 'none'}")
    print(f"Still failing    : {comparison['still_failing'] or 'none'}")


def release_memo(baseline: Dict[str, Any], improved: Dict[str, Any], holdout: Dict[str, Any]) -> str:
    """PASS only when the improved version clears the gate on both the frozen
    suite and the unseen holdout, and every run was live."""
    live = all(record["meta"]["mode"] == "live" for record in (baseline, improved, holdout))
    passes = release_decision(improved["summary"]) == "PASS" and release_decision(holdout["summary"]) == "PASS"
    decision = "PASS" if live and passes else "BLOCK"

    def line(label: str, summary: Dict[str, Any]) -> str:
        return (
            f"- {label}: {summary['run_pass_rate']:.1%} run pass; "
            f"{summary['stable_case_rate']:.1%} stable cases; {summary['critical_pass_rate']:.1%} critical pass."
        )

    reason = ""
    if not live:
        reason = "\nBlocked because at least one input was replay fixtures, which cannot authorize a release."
    elif not passes:
        reason = "\nBlocked because the frozen suite or the holdout missed the predefined gate."

    return f"""Release decision: {decision}{reason}

Evidence from these runs (gate: {RELEASE_GATE}):
{line("Baseline", baseline["summary"])}
{line("Improved", improved["summary"])}
{line("Holdout ", holdout["summary"])}

Interpretation:
A pass supports a controlled pilot under the tested conditions, followed by monitoring of new live
traces. It does not establish unrestricted production reliability: broader release needs more
cases, human calibration of the phrase-based checks, and adversarial and privacy testing."""
