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
    ReviewResult,
    ReviewVerdict,
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


def _approved_review() -> ReviewResult:
    return ReviewResult(verdict=ReviewVerdict.APPROVED, rationale="findings are consistent")


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
        lambda self, use_case, risk, compliance, **kwargs: decision_result,
    )
    # The reviewer signs off by default, so pipeline tests that aren't about
    # the review loop don't need to know it exists (no revision, no OpenAI call).
    monkeypatch.setattr(
        "app.agents.review_agent.ReviewAgent.review",
        lambda self, use_case, risk, compliance, decision: _approved_review(),
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
        "review",
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
# Review loop: Decision -> Review -> (revise Decision -> Review)*
# =========================================================


def _needs_revision(*issues: str) -> ReviewResult:
    return ReviewResult(
        verdict=ReviewVerdict.NEEDS_REVISION,
        issues=list(issues) or ["decision contradicts the compliance status"],
        suggestions=["require human review before go-live"],
        rationale="the decision is not supported by the findings",
    )


def _script_review_loop(monkeypatch, decisions, reviews):
    """Replace the Decision and Review agents with scripted sequences and
    record every call, so tests can assert what was sent back for revision."""
    decisions, reviews = list(decisions), list(reviews)
    decide_calls, review_calls = [], []

    def _decide(self, use_case, risk, compliance, previous_decision=None, review=None):
        decide_calls.append({"previous_decision": previous_decision, "review": review})
        return decisions.pop(0)

    def _review(self, use_case, risk, compliance, decision):
        review_calls.append(decision)
        return reviews.pop(0)

    monkeypatch.setattr("app.agents.decision_agent.DecisionAgent.decide", _decide)
    monkeypatch.setattr("app.agents.review_agent.ReviewAgent.review", _review)
    return decide_calls, review_calls


def _decision(decision: Decision, conditions=None, rationale="r") -> DecisionResult:
    return DecisionResult(decision=decision, conditions=conditions or [], rationale=rationale)


def test_review_needs_revision_then_approved_uses_revised_decision(monkeypatch):
    _patch_agents(monkeypatch, decision=Decision.APPROVE)
    flawed = _decision(Decision.APPROVE, rationale="looks fine")
    revised = _decision(Decision.REQUIRE_HUMAN_APPROVAL, ["add human review"], "non-compliant")
    first_review = _needs_revision("approve contradicts non_compliant status")
    decide_calls, review_calls = _script_review_loop(
        monkeypatch, [flawed, revised], [first_review, _approved_review()]
    )
    use_case = _use_case()

    report = GovernanceOrchestrator().run(use_case)

    assert report.decision.decision == Decision.REQUIRE_HUMAN_APPROVAL
    assert report.status == ReportStatus.PENDING_HUMAN_APPROVAL

    # Only the second call is a revision, and it gets its own previous answer
    # plus the reviewer's critique of it.
    assert decide_calls[0] == {"previous_decision": None, "review": None}
    assert decide_calls[1] == {"previous_decision": flawed, "review": first_review}
    # The reviewer saw the original decision first, then the revised one.
    assert review_calls == [flawed, revised]

    entries = get_audit_log(use_case.id)
    assert [e["stage"] for e in entries] == [
        "intake",
        "risk_assessment",
        "policy_compliance",
        "decision",
        "review",
        "decision_revision",
        "review",
        "report_finalized",
    ]
    reviews = [e["data"] for e in entries if e["stage"] == "review"]
    assert [r["verdict"] for r in reviews] == ["needs_revision", "approved"]
    revision_entry = next(e for e in entries if e["stage"] == "decision_revision")
    assert revision_entry["data"]["decision"] == "require_human_approval"


def test_unresolved_review_escalates_approve_to_human_approval(monkeypatch):
    _patch_agents(monkeypatch, decision=Decision.APPROVE)
    decide_calls, _ = _script_review_loop(
        monkeypatch,
        [_decision(Decision.APPROVE), _decision(Decision.APPROVE)],
        [_needs_revision("still wrong"), _needs_revision("still wrong")],
    )
    use_case = _use_case()

    report = GovernanceOrchestrator().run(use_case)

    assert len(decide_calls) == 2  # initial + the one allowed revision
    assert report.decision.decision == Decision.REQUIRE_HUMAN_APPROVAL
    assert report.status == ReportStatus.PENDING_HUMAN_APPROVAL
    assert "auto-escalated" in report.decision.rationale
    assert "still wrong" in report.decision.rationale

    stages = [e["stage"] for e in get_audit_log(use_case.id)]
    assert stages[-2:] == ["review_escalation", "report_finalized"]


def test_unresolved_review_does_not_downgrade_a_block(monkeypatch):
    _patch_agents(monkeypatch, decision=Decision.BLOCK)
    _script_review_loop(
        monkeypatch,
        [_decision(Decision.BLOCK), _decision(Decision.BLOCK)],
        [_needs_revision(), _needs_revision()],
    )
    use_case = _use_case()

    report = GovernanceOrchestrator().run(use_case)

    assert report.decision.decision == Decision.BLOCK
    assert report.status == ReportStatus.BLOCKED
    assert "review_escalation" not in [e["stage"] for e in get_audit_log(use_case.id)]


def test_zero_max_revisions_reviews_but_never_revises(monkeypatch):
    _patch_agents(monkeypatch, decision=Decision.APPROVE)
    monkeypatch.setattr("app.config.MAX_REVIEW_REVISIONS", 0)
    decide_calls, review_calls = _script_review_loop(
        monkeypatch, [_decision(Decision.APPROVE)], [_needs_revision()]
    )
    use_case = _use_case()

    report = GovernanceOrchestrator().run(use_case)

    assert len(decide_calls) == 1
    assert len(review_calls) == 1
    # With no revision budget the unresolved review still can't auto-approve.
    assert report.decision.decision == Decision.REQUIRE_HUMAN_APPROVAL
    assert "decision_revision" not in [e["stage"] for e in get_audit_log(use_case.id)]


def test_review_that_approves_first_time_leaves_decision_untouched(monkeypatch):
    _patch_agents(monkeypatch, decision=Decision.APPROVE)
    original = _decision(Decision.APPROVE, rationale="low risk, compliant")
    decide_calls, review_calls = _script_review_loop(monkeypatch, [original], [_approved_review()])

    report = GovernanceOrchestrator().run(_use_case())

    assert len(decide_calls) == 1
    assert len(review_calls) == 1
    assert report.decision == original
    assert report.status == ReportStatus.COMPLETED


# =========================================================
# Long-term memory: similar past cases are recalled as precedent
# =========================================================

LOAN_CASE = dict(
    name="Automated Loan Denial Assistant",
    description="Autonomous agent that approves or denies consumer loan applications with no human review.",
)
SIMILAR_CASE = dict(
    name="Credit Application Screening Bot",
    description="Autonomous agent that screens consumer loan applications and rejects applicants without human review.",
)


def _spy_on_memory(monkeypatch, decision: Decision):
    """Stub the agents (as `_patch_agents` does) but record the
    `memory_context` each one held when it ran."""
    risk_result, compliance_result, decision_result = _patch_agents(monkeypatch, decision=decision)
    seen = {}

    def _assess(self, use_case):
        seen["risk"] = self.memory_context
        return risk_result

    def _check(self, use_case, risk):
        seen["policy"] = self.memory_context
        return compliance_result

    def _decide(self, use_case, risk, compliance, **kwargs):
        seen["decision"] = self.memory_context
        return decision_result

    def _review(self, use_case, risk, compliance, decision):
        seen["review"] = self.memory_context
        return _approved_review()

    monkeypatch.setattr("app.agents.risk_agent.RiskAssessmentAgent.assess", _assess)
    monkeypatch.setattr("app.agents.policy_agent.PolicyComplianceAgent.check", _check)
    monkeypatch.setattr("app.agents.decision_agent.DecisionAgent.decide", _decide)
    monkeypatch.setattr("app.agents.review_agent.ReviewAgent.review", _review)
    return seen


def test_similar_past_case_is_recalled_for_every_agent_and_audited(monkeypatch):
    seen = _spy_on_memory(monkeypatch, decision=Decision.BLOCK)
    orchestrator = GovernanceOrchestrator()

    first = _use_case(**LOAN_CASE)
    orchestrator.run(first)
    assert seen == {"risk": "", "policy": "", "decision": "", "review": ""}  # nothing to recall yet
    assert "memory_retrieval" not in [e["stage"] for e in get_audit_log(first.id)]

    second = _use_case(**SIMILAR_CASE)
    orchestrator.run(second)

    for agent, context in seen.items():
        assert "Relevant Past Cases" in context, agent
        assert "Case: Automated Loan Denial Assistant" in context, agent
        assert "Decision: block" in context, agent

    entries = get_audit_log(second.id)
    assert [e["stage"] for e in entries][:3] == ["intake", "memory_retrieval", "risk_assessment"]
    (recall,) = [e for e in entries if e["stage"] == "memory_retrieval"]
    assert [c["use_case_id"] for c in recall["data"]["cases"]] == [first.id]


def test_human_verdict_is_remembered_and_recalled_by_later_cases(monkeypatch):
    seen = _spy_on_memory(monkeypatch, decision=Decision.REQUIRE_HUMAN_APPROVAL)
    orchestrator = GovernanceOrchestrator()

    first = _use_case(**LOAN_CASE)
    orchestrator.run(first)
    orchestrator.apply_human_decision(
        first.id, approved=False, approver="jane@example.com", notes="no bias testing yet"
    )

    orchestrator.run(_use_case(**SIMILAR_CASE))

    context = seen["decision"]
    assert "Status: rejected_by_human" in context
    assert "Human review: rejected - no bias testing yet" in context
    assert "jane@example.com" not in context


def test_memory_outage_does_not_break_the_pipeline(monkeypatch):
    """E.g. the agent_memory migration hasn't been applied yet: reads and
    writes to that table fail, and governance carries on without memory."""
    from app import db
    from app.memory import TABLE as MEMORY_TABLE

    _patch_agents(monkeypatch, decision=Decision.REQUIRE_HUMAN_APPROVAL)
    real_select, real_upsert = db.select, db.upsert

    def select(table, params=None):
        if table == MEMORY_TABLE:
            raise RuntimeError(f"relation {table} does not exist")
        return real_select(table, params)

    def upsert(table, row, on_conflict):
        if table == MEMORY_TABLE:
            raise RuntimeError(f"relation {table} does not exist")
        return real_upsert(table, row, on_conflict)

    monkeypatch.setattr(db, "select", select)
    monkeypatch.setattr(db, "upsert", upsert)

    orchestrator = GovernanceOrchestrator()
    for fields in (LOAN_CASE, SIMILAR_CASE):
        use_case = _use_case(**fields)
        report = orchestrator.run(use_case)

        assert report.status == ReportStatus.PENDING_HUMAN_APPROVAL
        assert load_report(use_case.id) is not None
        assert [e["stage"] for e in get_audit_log(use_case.id)] == [
            "intake",
            "risk_assessment",
            "policy_compliance",
            "decision",
            "review",
            "report_finalized",
        ]

    # ... and a human decision still goes through.
    orchestrator.apply_human_decision(use_case.id, approved=True, approver="jane")


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
        "review",
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
