"""Command-line interface for the GovernAI platform.

Examples:
    python -m app.cli submit examples/sample_use_case.json
    python -m app.cli show <use_case_id>
    python -m app.cli list
    python -m app.cli approve <use_case_id> --approver "jane@example.com" --approve
    python -m app.cli approve <use_case_id> --approver "jane@example.com" --reject --notes "needs bias testing first"
    python -m app.cli audit-log [<use_case_id>]
"""
import argparse
import json
import sys

from app.llm_client import ConfigurationError
from app.models import AIUseCase
from app.orchestrator import (
    GovernanceOrchestrator,
    InvalidApprovalStateError,
    UseCaseNotFoundError,
)
from app.reports import list_reports, load_report
from app.tools.audit_log import get_audit_log


def _print_report(report) -> None:
    print(json.dumps(json.loads(report.model_dump_json()), indent=2))


def cmd_submit(args: argparse.Namespace) -> int:
    with open(args.file, "r", encoding="utf-8") as f:
        payload = json.load(f)
    use_case = AIUseCase(**payload)
    print(f"Submitting use case '{use_case.name}' (id={use_case.id}) via model "
          f"'{args.model or 'default'}'...", file=sys.stderr)
    try:
        orchestrator = GovernanceOrchestrator(model=args.model)
        report = orchestrator.run(use_case)
    except ConfigurationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    _print_report(report)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    report = load_report(args.use_case_id)
    if report is None:
        print(f"No report found for use case {args.use_case_id}", file=sys.stderr)
        return 1
    _print_report(report)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for report in list_reports():
        print(f"{report.use_case.id}  [{report.status.value:>20}]  "
              f"risk={report.risk_assessment.risk_level.value:<8}  {report.use_case.name}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    orchestrator = GovernanceOrchestrator()
    try:
        report = orchestrator.apply_human_decision(
            args.use_case_id, approved=args.approve, approver=args.approver, notes=args.notes
        )
    except UseCaseNotFoundError:
        print(f"No report found for use case {args.use_case_id}", file=sys.stderr)
        return 1
    except InvalidApprovalStateError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    _print_report(report)
    return 0


def cmd_audit_log(args: argparse.Namespace) -> int:
    for entry in get_audit_log(args.use_case_id):
        print(json.dumps(entry))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="governai", description="Multi-Agent AI Governance Platform CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_submit = sub.add_parser("submit", help="Submit a new AI use case for governance review")
    p_submit.add_argument("file", help="Path to a JSON file describing the use case")
    p_submit.add_argument("--model", default=None, help="Override the OpenRouter model slug")
    p_submit.set_defaults(func=cmd_submit)

    p_show = sub.add_parser("show", help="Show the governance report for a use case")
    p_show.add_argument("use_case_id")
    p_show.set_defaults(func=cmd_show)

    p_list = sub.add_parser("list", help="List all governance reports")
    p_list.set_defaults(func=cmd_list)

    p_approve = sub.add_parser("approve", help="Record a human approval/rejection decision")
    p_approve.add_argument("use_case_id")
    p_approve.add_argument("--approver", required=True, help="Identifier of the approving human")
    p_approve.add_argument("--notes", default=None)
    group = p_approve.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", dest="approve", action="store_true")
    group.add_argument("--reject", dest="approve", action="store_false")
    p_approve.set_defaults(func=cmd_approve)

    p_audit = sub.add_parser("audit-log", help="Print audit log entries")
    p_audit.add_argument("use_case_id", nargs="?", default=None)
    p_audit.set_defaults(func=cmd_audit_log)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
