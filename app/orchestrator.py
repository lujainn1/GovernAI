"""Orchestrates the end-to-end governance workflow:

    Input -> Risk Assessment -> Policy Check -> Decision
          -> Human Approval (if required) -> Audit Log

Optionally, the input can be a document upload instead of pasted-in
documentation:

    Document Upload -> Document Processing Agent
                     -> Risk Assessment -> Policy Check -> Decision
                     -> Human Approval (if required) -> Audit Log

Each stage's output is written to the audit log, and the resulting
GovernanceReport is persisted so it can be looked up or approved later.
"""
from typing import Optional, Tuple

from app.agents.decision_agent import DecisionAgent
from app.agents.document_agent import DocumentProcessingAgent
from app.agents.policy_agent import PolicyComplianceAgent
from app.agents.risk_agent import RiskAssessmentAgent
from app.models import (
    AIUseCase,
    Decision,
    DocumentProcessingResult,
    GovernanceReport,
    HumanApproval,
    ReportStatus,
    utcnow_iso,
)
from app.reports import load_report, save_report, save_use_case
from app.tools.audit_log import log_event


class UseCaseNotFoundError(LookupError):
    pass


class InvalidApprovalStateError(RuntimeError):
    pass


class GovernanceOrchestrator:
    """Runs the governance pipeline. Agents (and the OpenAI client they
    need) are created lazily, so simply instantiating the orchestrator to
    call `apply_human_decision` does not require an API key to be set."""

    def __init__(self, model: Optional[str] = None):
        self._model = model
        self._document_agent: Optional[DocumentProcessingAgent] = None
        self._risk_agent: Optional[RiskAssessmentAgent] = None
        self._policy_agent: Optional[PolicyComplianceAgent] = None
        self._decision_agent: Optional[DecisionAgent] = None

    @property
    def document_agent(self) -> DocumentProcessingAgent:
        if self._document_agent is None:
            self._document_agent = DocumentProcessingAgent()
        return self._document_agent

    @property
    def risk_agent(self) -> RiskAssessmentAgent:
        if self._risk_agent is None:
            self._risk_agent = RiskAssessmentAgent(model=self._model)
        return self._risk_agent

    @property
    def policy_agent(self) -> PolicyComplianceAgent:
        if self._policy_agent is None:
            self._policy_agent = PolicyComplianceAgent(model=self._model)
        return self._policy_agent

    @property
    def decision_agent(self) -> DecisionAgent:
        if self._decision_agent is None:
            self._decision_agent = DecisionAgent(model=self._model)
        return self._decision_agent

    def run(self, use_case: AIUseCase, created_by: Optional[str] = None) -> GovernanceReport:
        # Persist the use case first: audit_log.use_case_id foreign-keys to
        # use_cases, so a row must exist before the first log_event call.
        save_use_case(use_case, created_by=created_by)
        log_event(use_case.id, "intake", "system", use_case.model_dump(mode="json"))

        risk_result = self.risk_agent.assess(use_case)
        log_event(
            use_case.id, "risk_assessment", self.risk_agent.name, risk_result.model_dump(mode="json")
        )

        compliance_result = self.policy_agent.check(use_case, risk_result)
        log_event(
            use_case.id,
            "policy_compliance",
            self.policy_agent.name,
            compliance_result.model_dump(mode="json"),
        )

        decision_result = self.decision_agent.decide(use_case, risk_result, compliance_result)
        log_event(
            use_case.id, "decision", self.decision_agent.name, decision_result.model_dump(mode="json")
        )

        if decision_result.decision == Decision.REQUIRE_HUMAN_APPROVAL:
            status = ReportStatus.PENDING_HUMAN_APPROVAL
        elif decision_result.decision == Decision.BLOCK:
            status = ReportStatus.BLOCKED
        else:
            status = ReportStatus.COMPLETED

        report = GovernanceReport(
            use_case=use_case,
            risk_assessment=risk_result,
            policy_compliance=compliance_result,
            decision=decision_result,
            status=status,
        )
        save_report(report)
        log_event(use_case.id, "report_finalized", "system", {"status": status.value})
        return report

    def run_with_document(
        self,
        use_case: AIUseCase,
        filename: str,
        content: bytes,
        created_by: Optional[str] = None,
    ) -> Tuple[GovernanceReport, DocumentProcessingResult]:
        """Same pipeline as `run`, but the use case's documentation comes
        from an uploaded file instead of (or in addition to) pasted-in
        text:

            Document Upload -> Document Processing Agent
                             -> Risk Assessment -> Policy Check -> Decision

        The Document Processing Agent extracts the text and detects its
        language *before* the use case is persisted or any other agent
        runs. Its output is folded into `use_case.documentation` - the
        same field the Risk and Policy agents already read via
        `app.tools.document_analysis.analyze_document` - so this method
        does not touch those agents or the Decision agent at all; it just
        feeds them through the field they already consume.

        Raises:
            UnsupportedFileTypeError: extension isn't .pdf or .docx.
            DocumentExtractionError: the file is corrupted/unparsable.
            DocumentProcessingError: an unexpected failure while analyzing
                the extracted text.
            (all raised by DocumentProcessingAgent.process; none of them
            persist a use case or write an audit entry, so a failed
            upload leaves no partial state behind.)
        """
        doc_result = self.document_agent.process(filename, content)

        # Persist early so the document_processing audit entry (which
        # needs use_cases.id to exist, per its foreign key) lands before
        # the "intake" entry that `run` will add for the same use case.
        save_use_case(use_case, created_by=created_by)
        log_event(
            use_case.id,
            "document_processing",
            self.document_agent.name,
            doc_result.model_dump(mode="json"),
        )

        extracted = DocumentProcessingAgent.to_documentation(doc_result)
        use_case.documentation = (
            f"{use_case.documentation}\n\n{extracted}" if use_case.documentation else extracted
        )

        report = self.run(use_case, created_by=created_by)
        return report, doc_result

    def apply_human_decision(
        self, use_case_id: str, approved: bool, approver: str, notes: Optional[str] = None
    ) -> GovernanceReport:
        report = load_report(use_case_id)
        if report is None:
            raise UseCaseNotFoundError(use_case_id)
        if report.status != ReportStatus.PENDING_HUMAN_APPROVAL:
            raise InvalidApprovalStateError(
                f"Use case {use_case_id} is not awaiting human approval (status={report.status.value})"
            )

        report.human_approval = HumanApproval(approved=approved, approver=approver, notes=notes)
        report.status = ReportStatus.APPROVED_BY_HUMAN if approved else ReportStatus.REJECTED_BY_HUMAN
        report.updated_at = utcnow_iso()
        save_report(report)

        log_event(
            use_case_id,
            "human_approval",
            approver,
            {"approved": approved, "notes": notes, "resulting_status": report.status.value},
        )
        return report
