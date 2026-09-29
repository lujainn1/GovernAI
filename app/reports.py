"""Persistence for AI use cases and their GovernanceReport, backed by the
Supabase `use_cases` and `governance_reports` tables (one governance_reports
row per use case, related by use_case_id)."""
from typing import List, Optional

import httpx

from app import db
from app.models import (
    AIUseCase,
    ComplianceStatus,
    Decision,
    DecisionResult,
    GovernanceReport,
    HumanApproval,
    PolicyComplianceResult,
    ReportStatus,
    RiskAssessmentResult,
    RiskLevel,
    SuggestedRiskRule,
)

USE_CASES_TABLE = "use_cases"
REPORTS_TABLE = "governance_reports"

# The list views never show a use case's `documentation` - it can be a whole
# extracted PDF - so list queries leave it out instead of hauling it from the
# database through the API to the browser. Detail lookups still return it.
USE_CASE_LIST_COLUMNS = (
    "id,name,description,owner,data_classification,deployment_context,autonomy_level"
)


def _report_row(report: GovernanceReport) -> dict:
    return {
        "use_case_id": report.use_case.id,
        "risk_level": report.risk_assessment.risk_level.value,
        "risk_score": report.risk_assessment.risk_score,
        "risk_factors": report.risk_assessment.risk_factors,
        "risk_rationale": report.risk_assessment.rationale,
        "risk_suggested_new_rules": [
            r.model_dump(mode="json") for r in report.risk_assessment.suggested_new_rules
        ],
        "compliance_status": report.policy_compliance.status.value,
        "violated_policies": report.policy_compliance.violated_policies,
        "satisfied_policies": report.policy_compliance.satisfied_policies,
        "compliance_rationale": report.policy_compliance.rationale,
        "decision": report.decision.decision.value,
        "decision_conditions": report.decision.conditions,
        "decision_rationale": report.decision.rationale,
        "status": report.status.value,
        "human_approval": (
            report.human_approval.model_dump(mode="json") if report.human_approval else None
        ),
        "created_at": report.created_at,
        "updated_at": report.updated_at,
    }


def _report_from_rows(use_case_row: dict, report_row: dict) -> GovernanceReport:
    return GovernanceReport(
        use_case=AIUseCase(**use_case_row),
        risk_assessment=RiskAssessmentResult(
            risk_level=RiskLevel(report_row["risk_level"]),
            risk_score=report_row["risk_score"],
            risk_factors=report_row.get("risk_factors") or [],
            rationale=report_row["risk_rationale"],
            suggested_new_rules=[
                SuggestedRiskRule(**r) for r in (report_row.get("risk_suggested_new_rules") or [])
            ],
        ),
        policy_compliance=PolicyComplianceResult(
            status=ComplianceStatus(report_row["compliance_status"]),
            violated_policies=report_row.get("violated_policies") or [],
            satisfied_policies=report_row.get("satisfied_policies") or [],
            rationale=report_row["compliance_rationale"],
        ),
        decision=DecisionResult(
            decision=Decision(report_row["decision"]),
            conditions=report_row.get("decision_conditions") or [],
            rationale=report_row["decision_rationale"],
        ),
        status=ReportStatus(report_row["status"]),
        created_at=report_row["created_at"],
        updated_at=report_row["updated_at"],
        human_approval=(
            HumanApproval(**report_row["human_approval"]) if report_row.get("human_approval") else None
        ),
    )


def save_use_case(use_case: AIUseCase, created_by: Optional[str] = None) -> None:
    """Persist the use case itself. Called before any agent runs so the
    audit log (which foreign-keys to use_cases) always has a row to point at."""

    row = use_case.model_dump(mode="json")
    if created_by:
        row["created_by"] = created_by
    db.upsert(USE_CASES_TABLE, row, on_conflict="id")


def save_report(report: GovernanceReport) -> None:
    db.upsert(REPORTS_TABLE, _report_row(report), on_conflict="use_case_id")


def load_report(use_case_id: str) -> Optional[GovernanceReport]:
    try:
        use_case_rows = db.select(USE_CASES_TABLE, {"id": f"eq.{use_case_id}"})
    except httpx.HTTPStatusError as exc:
        # An id that is not a UUID cannot match any row; PostgREST reports
        # that as a 400 rather than an empty result. That is a bad request
        # from the caller, not a database failure, so it must read as "no
        # such use case" and reach the route's 404 instead of a 500.
        if db.is_invalid_value_error(exc):
            return None
        raise
    if not use_case_rows:
        return None

    report_rows = db.select(REPORTS_TABLE, {"use_case_id": f"eq.{use_case_id}"})
    if not report_rows:
        return None

    return _report_from_rows(use_case_rows[0], report_rows[0])


def list_reports() -> List[GovernanceReport]:
    use_cases_by_id = {
        row["id"]: row for row in db.select(USE_CASES_TABLE, {"select": USE_CASE_LIST_COLUMNS})
    }
    report_rows = db.select(REPORTS_TABLE, {"order": "created_at.asc"})

    reports = []
    for report_row in report_rows:
        use_case_row = use_cases_by_id.get(report_row["use_case_id"])
        if use_case_row is None:
            continue
        reports.append(_report_from_rows(use_case_row, report_row))
    return reports
