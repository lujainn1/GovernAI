import pytest

from app.models import (
    AIUseCase,
    ComplianceStatus,
    Decision,
    DecisionResult,
    PolicyComplianceResult,
    ReportStatus,
    RiskAssessmentResult,
    RiskLevel,
)
from app.orchestrator import (
    GovernanceOrchestrator,
    InvalidApprovalStateError,
    UseCaseNotFoundError,
)
from app.reports import load_report
from app.tools.audit_log import get_audit_log


def _use_case(**overrides) -> AIUseCase:
    defaults = dict(name="Test Use Case", description="desc", owner="team-x")
    defaults.update(overrides)
    return AIUseCase(**defaults)


def _patch_agents(monkeypatch, decision: Decision, risk_level: RiskLevel = RiskLevel.HIGH,
                   compliance_status: ComplianceStatus = ComplianceStatus.NON_COMPLIANT):
    risk_result = RiskAssessmentResult(
        risk_level=risk_level, risk_score=80, risk_factors=["autonomy"], rationale="high autonomy"
    )
    compliance_result = PolicyComplianceResult(
        status=compliance_status,
        violated_policies=["POL-003"],
        satisfied_policies=[],
        rationale="missing human oversight",
    )
    decision_result = DecisionResult(decision=decision, conditions=["add human review"], rationale="risky")

    monkeypatch.setattr(
        "app.agents.risk_agent.RiskAssessmentAgent.assess", lambda self, use_case: risk_result
    )
    monkeypatch.setattr(
        "app.agents.policy_agent.PolicyComplianceAgent.check",
        lambda self, use_case, risk: compliance_result,
    )
    monkeypatch.setattr(
        "app.agents.decision_agent.DecisionAgent.decide",
        lambda self, use_case, risk, compliance: decision_result,
    )
    return risk_result, compliance_result, decision_result


def test_run_pipeline_require_human_approval(monkeypatch):
    _patch_agents(monkeypatch, decision=Decision.REQUIRE_HUMAN_APPROVAL)
    use_case = _use_case()

    orchestrator = GovernanceOrchestrator()
    report = orchestrator.run(use_case)

    assert report.status == ReportStatus.PENDING_HUMAN_APPROVAL
    assert report.decision.decision == Decision.REQUIRE_HUMAN_APPROVAL
    assert load_report(use_case.id) is not None

    stages = [e["stage"] for e in get_audit_log(use_case.id)]
    assert stages == [
        "intake",
        "risk_assessment",
        "policy_compliance",
        "decision",
        "report_finalized",
    ]


def test_run_pipeline_block(monkeypatch):
    _patch_agents(monkeypatch, decision=Decision.BLOCK)
    use_case = _use_case()

    report = GovernanceOrchestrator().run(use_case)
    assert report.status == ReportStatus.BLOCKED


def test_run_pipeline_approve(monkeypatch):
    _patch_agents(
        monkeypatch,
        decision=Decision.APPROVE,
        risk_level=RiskLevel.LOW,
        compliance_status=ComplianceStatus.COMPLIANT,
    )
    use_case = _use_case()

    report = GovernanceOrchestrator().run(use_case)
    assert report.status == ReportStatus.COMPLETED


def test_human_approval_flow(monkeypatch):
    _patch_agents(monkeypatch, decision=Decision.REQUIRE_HUMAN_APPROVAL)
    use_case = _use_case()
    orchestrator = GovernanceOrchestrator()
    orchestrator.run(use_case)

    approved_report = orchestrator.apply_human_decision(
        use_case.id, approved=True, approver="jane@example.com", notes="looks fine now"
    )
    assert approved_report.status == ReportStatus.APPROVED_BY_HUMAN
    assert approved_report.human_approval.approver == "jane@example.com"

    audit_entries = get_audit_log(use_case.id)
    assert audit_entries[-1]["stage"] == "human_approval"


def test_apply_human_decision_requires_pending_status(monkeypatch):
    _patch_agents(monkeypatch, decision=Decision.APPROVE, risk_level=RiskLevel.LOW,
                   compliance_status=ComplianceStatus.COMPLIANT)
    use_case = _use_case()
    orchestrator = GovernanceOrchestrator()
    orchestrator.run(use_case)  # already COMPLETED, not pending

    with pytest.raises(InvalidApprovalStateError):
        orchestrator.apply_human_decision(use_case.id, approved=True, approver="jane")


def test_apply_human_decision_unknown_use_case():
    orchestrator = GovernanceOrchestrator()
    with pytest.raises(UseCaseNotFoundError):
        orchestrator.apply_human_decision("does-not-exist", approved=True, approver="jane")
