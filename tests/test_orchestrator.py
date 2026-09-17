from io import BytesIO

import pytest
from docx import Document

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
from app.tools.document_extract import UnsupportedFileTypeError


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


# =========================================================
# Document upload -> Document Agent -> Orchestrator -> Risk + Policy -> Decision
# =========================================================


def _docx_bytes(text: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_run_with_document_feeds_extracted_text_through_full_pipeline(monkeypatch):
    """End-to-end: Upload -> Document Agent -> GovernanceOrchestrator ->
    Policy Agent + Risk Agent -> Decision Agent -> Final Result, using the
    real (non-LLM) Document Processing Agent and mocked Risk/Policy/
    Decision agents so the test doesn't need an OpenAI call."""
    risk_result, compliance_result, decision_result = _patch_agents(
        monkeypatch, decision=Decision.REQUIRE_HUMAN_APPROVAL
    )

    # Capture what the Risk/Policy agents actually saw, to prove the
    # extracted document text reached them via use_case.documentation.
    seen_documentation = {}

    def _assess(self, use_case):
        seen_documentation["risk"] = use_case.documentation
        return risk_result

    def _check(self, use_case, risk):
        seen_documentation["policy"] = use_case.documentation
        return compliance_result

    monkeypatch.setattr("app.agents.risk_agent.RiskAssessmentAgent.assess", _assess)
    monkeypatch.setattr("app.agents.policy_agent.PolicyComplianceAgent.check", _check)

    use_case = _use_case(documentation=None)
    content = _docx_bytes("This Privacy Policy describes how we process personal data.")

    orchestrator = GovernanceOrchestrator()
    report, doc_result = orchestrator.run_with_document(use_case, "policy.docx", content)

    # Document Processing Agent output.
    assert doc_result.detected_language.value == "english"
    assert doc_result.file_type == "docx"
    assert "Privacy Policy" in doc_result.extracted_text

    # Extracted text reached the Risk and Policy agents unchanged in spirit.
    assert "Privacy Policy" in seen_documentation["risk"]
    assert "Privacy Policy" in seen_documentation["policy"]

    # Decision agent still produced the final result via the normal pipeline.
    assert report.decision.decision == Decision.REQUIRE_HUMAN_APPROVAL
    assert report.status == ReportStatus.PENDING_HUMAN_APPROVAL
    assert load_report(use_case.id) is not None

    stages = [e["stage"] for e in get_audit_log(use_case.id)]
    assert stages == [
        "document_processing",
        "intake",
        "risk_assessment",
        "policy_compliance",
        "decision",
        "report_finalized",
    ]


def test_run_with_document_unsupported_file_type_persists_nothing(monkeypatch):
    """A failed upload must not leave a partial use case / audit trail
    behind - the Document Processing Agent runs before anything is saved."""
    _patch_agents(monkeypatch, decision=Decision.APPROVE, risk_level=RiskLevel.LOW,
                   compliance_status=ComplianceStatus.COMPLIANT)
    use_case = _use_case()

    orchestrator = GovernanceOrchestrator()
    with pytest.raises(UnsupportedFileTypeError):
        orchestrator.run_with_document(use_case, "notes.txt", b"plain text, not pdf/docx")

    assert load_report(use_case.id) is None
    assert get_audit_log(use_case.id) == []
