"""Persistence for GovernanceReport objects (one JSON file per use case)."""
from typing import List, Optional

from app import config
from app.models import GovernanceReport


def save_report(report: GovernanceReport) -> None:
    path = config.REPORTS_DIR / f"{report.use_case.id}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def load_report(use_case_id: str) -> Optional[GovernanceReport]:
    path = config.REPORTS_DIR / f"{use_case_id}.json"
    if not path.exists():
        return None
    return GovernanceReport.model_validate_json(path.read_text(encoding="utf-8"))


def list_reports() -> List[GovernanceReport]:
    reports = []
    for path in sorted(config.REPORTS_DIR.glob("*.json")):
        reports.append(GovernanceReport.model_validate_json(path.read_text(encoding="utf-8")))
    return reports
