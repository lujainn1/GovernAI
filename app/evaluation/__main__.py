"""Command-line interface for the agent evaluation workflow.

Examples:
    python -m app.evaluation run --version baseline --repeats 3
    python -m app.evaluation run --version gpt4o --model gpt-4o --repeats 3
    python -m app.evaluation run --version gpt4o --model gpt-4o --suite holdout
    python -m app.evaluation compare baseline gpt4o --holdout-version gpt4o
    python -m app.evaluation demo            # offline walkthrough on replay fixtures

Workflow: run baseline -> read the diagnosis -> change one thing -> run the
same frozen suite again -> run the holdout -> compare and read the memo.
"""
import argparse
import sys
from pathlib import Path

from app import config
from app.evaluation import report
from app.evaluation.cases import SUITES, load_suite
from app.evaluation.checks import evaluate_suite, summarize
from app.evaluation.runner import resolve_mode, run_suite
from app.llm_client import ConfigurationError


def _execute(args: argparse.Namespace, version: str, suite: str, mode: str) -> dict:
    cases = load_suite(suite)
    model = args.model or config.OPENAI_MODEL
    print(f"Running {len(cases)} cases x {args.repeats} repeats for '{version}' ({mode})...", file=sys.stderr)

    def progress(trace: dict) -> None:
        print(f"  {trace['case_id']} run {trace['run_id']}", file=sys.stderr)

    traces = run_suite(cases, version, args.repeats, mode, args.model, on_run=progress)
    results = evaluate_suite(cases, traces)
    record = report.build_record(version, suite, mode, model, args.repeats, summarize(results), results)
    if not args.no_save:
        path = report.results_path(Path(args.output_dir), version, suite)
        report.save_record(record, path)
        print(f"Saved {path}", file=sys.stderr)
    return record


def cmd_run(args: argparse.Namespace) -> int:
    try:
        mode = resolve_mode(args.mode)
    except ConfigurationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    try:
        record = _execute(args, args.version, args.suite, mode)
    except ConfigurationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    report.print_run_report(record)
    report.print_diagnosis(record)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    directory = Path(args.output_dir)
    baseline = report.load_record(report.results_path(directory, args.baseline, "frozen"))
    improved = report.load_record(report.results_path(directory, args.improved, "frozen"))
    holdout = report.load_record(report.results_path(directory, args.holdout_version or args.improved, "holdout"))
    report.print_comparison(report.compare_records(baseline, improved))
    print("\n" + report.release_memo(baseline, improved, holdout))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """The whole workflow on replay fixtures, so it runs with no API key."""
    args.mode, args.model, args.no_save = "replay", None, True  # fixtures are never persisted
    records = {}
    for version, suite in [("baseline", "frozen"), ("improved", "frozen"), ("improved", "holdout")]:
        records[(version, suite)] = _execute(args, version, suite, "replay")
    report.print_run_report(records[("baseline", "frozen")])
    report.print_diagnosis(records[("baseline", "frozen")])
    report.print_run_report(records[("improved", "frozen")])
    report.print_comparison(report.compare_records(records[("baseline", "frozen")], records[("improved", "frozen")]))
    report.print_run_report(records[("improved", "holdout")])
    print("\n" + report.release_memo(*(records[key] for key in [("baseline", "frozen"), ("improved", "frozen"), ("improved", "holdout")])))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.evaluation", description="GovernAI agent evaluation")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--output-dir", default=str(report.RESULTS_DIR), help="Where result files are saved/read")

    def runnable(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repeats", type=int, default=3, help="Runs per case; repeats expose inconsistency")

    p_run = sub.add_parser("run", help="Run a suite, score it, and diagnose failures")
    p_run.add_argument("--version", required=True, help="Label for the system version under test, e.g. baseline")
    p_run.add_argument("--suite", choices=sorted(SUITES), default="frozen")
    p_run.add_argument("--mode", choices=["auto", "live", "replay"], default="auto")
    p_run.add_argument("--model", default=None, help="Override the OpenAI model for this run")
    p_run.add_argument("--no-save", action="store_true", help="Do not write the result file")
    runnable(p_run)
    common(p_run)
    p_run.set_defaults(func=cmd_run)

    p_cmp = sub.add_parser("compare", help="Compare two saved versions and print the release memo")
    p_cmp.add_argument("baseline", help="Version label of the baseline frozen-suite run")
    p_cmp.add_argument("improved", help="Version label of the improved frozen-suite run")
    p_cmp.add_argument("--holdout-version", default=None, help="Version label of the holdout run (default: improved)")
    common(p_cmp)
    p_cmp.set_defaults(func=cmd_compare)

    p_demo = sub.add_parser("demo", help="Offline end-to-end walkthrough on replay fixtures")
    runnable(p_demo)
    p_demo.set_defaults(func=cmd_demo)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
