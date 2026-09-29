"""Run the labelled test cases in examples/test_cases through the full
governance pipeline and report how often the Decision Agent matches the
expected decision.

Folder name = expected decision:
    examples/test_cases/approve/*.json
    examples/test_cases/require_human_approval/*.json
    examples/test_cases/block/*.json

If a `<case>_*.docx|pdf` file sits next to a case JSON, the case is run
through the document flow (run_with_document) with that file attached.

Usage:
    python scripts/run_test_cases.py
    python scripts/run_test_cases.py --only block
    python scripts/run_test_cases.py --model gpt-4o --out results.csv

Needs the same environment as the app (OPENAI_API_KEY, SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY) because every run is persisted and audited.
"""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import AIUseCase  # noqa: E402
from app.orchestrator import GovernanceOrchestrator  # noqa: E402

CASES_DIR = Path(__file__).resolve().parent.parent / "examples" / "test_cases"
LABELS = ["approve", "require_human_approval", "block"]
DOC_EXTENSIONS = {".docx", ".pdf", ".pptx", ".xlsx", ".md", ".txt"}


def _attachment_for(case_path: Path):
    for candidate in sorted(case_path.parent.glob(f"{case_path.stem}_*")):
        if candidate.suffix.lower() in DOC_EXTENSIONS:
            return candidate
    return None


def _value(x):
    return getattr(x, "value", x)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=LABELS, help="Run only one expected-decision folder")
    parser.add_argument("--model", default=None, help="Override the LLM model")
    parser.add_argument("--out", default="test_case_results.csv", help="CSV file for per-case results")
    args = parser.parse_args()

    orchestrator = GovernanceOrchestrator(model=args.model)
    labels = [args.only] if args.only else LABELS
    rows = []

    for expected in labels:
        for case_path in sorted((CASES_DIR / expected).glob("*.json")):
            use_case = AIUseCase(**json.loads(case_path.read_text(encoding="utf-8")))
            attachment = _attachment_for(case_path)
            print(f"[{expected}] {case_path.name}{' + ' + attachment.name if attachment else ''} ...", flush=True)
            try:
                if attachment:
                    report, _ = orchestrator.run_with_document(use_case, attachment.name, attachment.read_bytes())
                else:
                    report = orchestrator.run(use_case)
                actual = _value(report.decision.decision)
                row = {
                    "case": f"{expected}/{case_path.stem}",
                    "expected": expected,
                    "actual": actual,
                    "match": actual == expected,
                    "risk_level": _value(report.risk_assessment.risk_level),
                    "risk_score": report.risk_assessment.risk_score,
                    "compliance": _value(report.policy_compliance.status),
                    "violated_policies": ";".join(report.policy_compliance.violated_policies),
                    "use_case_id": use_case.id,
                    "error": "",
                }
            except Exception as exc:  # keep going; one failure shouldn't stop the batch
                row = {"case": f"{expected}/{case_path.stem}", "expected": expected, "actual": "ERROR",
                       "match": False, "risk_level": "", "risk_score": "", "compliance": "",
                       "violated_policies": "", "use_case_id": use_case.id, "error": str(exc)[:300]}
            rows.append(row)
            print(f"    -> {row['actual']} (risk {row['risk_level']} {row['risk_score']})"
                  f"{'  OK' if row['match'] else '  MISMATCH'}", flush=True)

    if not rows:
        print("No test cases found.")
        return 1

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    total = len(rows)
    correct = sum(r["match"] for r in rows)
    print(f"\nAccuracy: {correct}/{total} = {correct / total:.0%}")

    confusion = Counter((r["expected"], r["actual"]) for r in rows)
    actual_cols = LABELS + (["ERROR"] if any(r["actual"] == "ERROR" for r in rows) else [])
    print("\nConfusion matrix (rows = expected, columns = actual)")
    print(f"{'':26}" + "".join(f"{c:>24}" for c in actual_cols))
    for exp in labels:
        print(f"{exp:26}" + "".join(f"{confusion[(exp, c)]:>24}" for c in actual_cols))

    # The most important safety check: nothing expected to be blocked is approved.
    unsafe = [r["case"] for r in rows if r["expected"] == "block" and r["actual"] == "approve"]
    print(f"\nBlock cases wrongly approved: {len(unsafe)}" + (f" -> {unsafe}" if unsafe else ""))
    print(f"Per-case results written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
