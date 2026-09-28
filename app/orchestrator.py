"""Orchestrates the end-to-end governance workflow:

    Input -> Risk Assessment -> Policy Check -> Decision -> Review
          -> Human Approval (if required) -> Audit Log

Before the agents run, the most similar past cases are recalled from
long-term memory (app.memory) and handed to all of them as precedent; each
finished case is remembered afterwards, and its remembered outcome is
updated when a human approves or rejects it.

The Review Agent critiques the Decision (and the findings behind it). If it
asks for a revision, the Decision Agent re-runs with the reviewer's feedback
and the result is reviewed again, up to `config.MAX_REVIEW_REVISIONS` times.

Optionally, the input can be a document upload instead of pasted-in
documentation:

    Document Upload -> Document Processing Agent
                     -> Risk Assessment -> Policy Check -> Decision -> Review
                     -> Human Approval (if required) -> Audit Log

Each stage's output is written to the audit log, and the resulting
GovernanceReport is persisted so it can be looked up or approved later.
"""
import logging
import time
from typing import Any, Callable, List, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel

from app import config
from app.agents.decision_agent import DecisionAgent
from app.agents.document_agent import DocumentProcessingAgent
from app.agents.policy_agent import PolicyComplianceAgent
from app.agents.review_agent import ReviewAgent
from app.agents.risk_agent import RiskAssessmentAgent
from app.memory import (
    format_memory_context,
    remember_case,
    retrieve_similar_cases,
    update_case_outcome,
)
from app.tools.coordination_diagnostics import diagnose_coordination
from app.models import (
    AIUseCase,
    Decision,
    DecisionResult,
    DocumentProcessingResult,
    GovernanceReport,
    HumanApproval,
    PipelineAgent,
    PipelineRun,
    PipelineRunStatus,
    PipelineStep,
    PolicyComplianceResult,
    ReportStatus,
    ReviewResult,
    ReviewVerdict,
    RiskAssessmentResult,
    StepStatus,
    utcnow_iso,
)
from app.observability.logging_config import emit
from app.observability.recorder import record_agent_run
from app.pipeline_runs import load_run, save_run
from app.reports import load_report, save_report, save_use_case
from app.tools.audit_log import log_event

logger = logging.getLogger("governai.orchestrator")

StageResult = TypeVar("StageResult")


class UseCaseNotFoundError(LookupError):
    pass


class InvalidApprovalStateError(RuntimeError):
    pass


class InvalidStepStateError(RuntimeError):
    """The step being approved/rejected is not the one awaiting a decision."""


_OutputModel = TypeVar("_OutputModel", bound=BaseModel)


def _escalate_unresolved_review(decision: DecisionResult, review: ReviewResult) -> DecisionResult:
    """Turn an `approve` the Review Agent never signed off on into
    `require_human_approval`, with a clearly-labeled note in the rationale
    (same auto-correction pattern as risk_agent._reconcile_with_bands)."""
    outstanding = "; ".join(review.issues) or review.rationale
    note = (
        "\n\n(Note: decision auto-escalated from 'approve' to 'require_human_approval': "
        "the Review Agent still had unresolved issues after the maximum number of "
        f"revisions. Outstanding: {outstanding})"
    )
    return decision.model_copy(
        update={
            "decision": Decision.REQUIRE_HUMAN_APPROVAL,
            "rationale": decision.rationale + note,
        }
    )


def _is_human_in_the_loop(use_case: AIUseCase) -> bool:
    level = (use_case.autonomy_level or "").strip().lower().replace("_", "-")
    return level == "human-in-the-loop"


def _escalate_for_human_in_the_loop(decision: DecisionResult) -> DecisionResult:
    """A use case declared human-in-the-loop has a person reviewing every
    output, so an `approve` must not auto-complete: turn it into
    `require_human_approval`, with a clearly-labeled note in the rationale."""
    note = (
        "\n\n(Note: decision auto-escalated from 'approve' to 'require_human_approval': "
        "this use case is declared human-in-the-loop, so a person signs off on it.)"
    )
    return decision.model_copy(
        update={
            "decision": Decision.REQUIRE_HUMAN_APPROVAL,
            "rationale": decision.rationale + note,
        }
    )


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
        self._review_agent: Optional[ReviewAgent] = None

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

    @property
    def review_agent(self) -> ReviewAgent:
        if self._review_agent is None:
            self._review_agent = ReviewAgent(model=self._model)
        return self._review_agent

    def _run_stage(
        self, use_case: AIUseCase, stage: str, agent: Any, call: Callable[[], StageResult]
    ) -> StageResult:
        """Run one agent stage, write its audit entry, and record the agent's
        execution trace linked to that entry. If the agent fails, the audit
        log gets a `<stage>_failed` entry (and the failed run is recorded
        against it) before the error propagates."""
        try:
            result = call()
        except Exception as exc:
            entry = log_event(
                use_case.id,
                f"{stage}_failed",
                agent.name,
                {"error_type": type(exc).__name__, "error": str(exc)[:500]},
            )
            record_agent_run(agent, stage=stage, use_case_id=use_case.id, audit_log_id=entry.id)
            raise
        entry = log_event(use_case.id, stage, agent.name, result.model_dump(mode="json"))
        record_agent_run(agent, stage=stage, use_case_id=use_case.id, audit_log_id=entry.id)
        return result

    def run(self, use_case: AIUseCase, created_by: Optional[str] = None) -> GovernanceReport:
        started = time.perf_counter()
        emit(logger, "pipeline_started", use_case_id=use_case.id)

        # Persist the use case first: audit_log.use_case_id foreign-keys to
        # use_cases, so a row must exist before the first log_event call.
        save_use_case(use_case, created_by=created_by)
        log_event(use_case.id, "intake", "system", use_case.model_dump(mode="json"))

        self._recall_similar_cases(use_case)

        risk_result = self._run_stage(
            use_case, "risk_assessment", self.risk_agent, lambda: self.risk_agent.assess(use_case)
        )
        compliance_result = self._run_stage(
            use_case,
            "policy_compliance",
            self.policy_agent,
            lambda: self.policy_agent.check(use_case, risk_result),
        )
        decision_result = self._run_stage(
            use_case,
            "decision",
            self.decision_agent,
            lambda: self.decision_agent.decide(use_case, risk_result, compliance_result),
        )

        decision_result = self._review_and_revise(
            use_case, risk_result, compliance_result, decision_result
        )

        report = self._finalize(use_case, risk_result, compliance_result, decision_result)
        emit(
            logger,
            "pipeline_finished",
            use_case_id=use_case.id,
            decision=report.decision.decision.value,
            status=report.status.value,
            risk_level=report.risk_assessment.risk_level.value,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return report

    def _finalize(
        self,
        use_case: AIUseCase,
        risk_result: RiskAssessmentResult,
        compliance_result: PolicyComplianceResult,
        decision_result: DecisionResult,
    ) -> GovernanceReport:
        """Turn the settled agent outputs into the persisted GovernanceReport
        (shared by `run` and the step-by-step flow)."""
        # Human-in-the-loop use cases always end at the human approve/reject
        # step. An agent `block` keeps its recommendation (`decision` stays
        # `block`) but is left pending so the person makes the final call.
        human_in_the_loop = _is_human_in_the_loop(use_case)
        if human_in_the_loop and decision_result.decision == Decision.APPROVE:
            decision_result = _escalate_for_human_in_the_loop(decision_result)
            log_event(
                use_case.id,
                "human_in_the_loop_escalation",
                "system",
                decision_result.model_dump(mode="json"),
            )

        if decision_result.decision == Decision.REQUIRE_HUMAN_APPROVAL or human_in_the_loop:
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
        remember_case(report)
        return report

    def _apply_memory_context(self, context: str) -> None:
        for agent in (self.risk_agent, self.policy_agent, self.decision_agent, self.review_agent):
            agent.memory_context = context

    def _recall_similar_cases(self, use_case: AIUseCase) -> str:
        """Recall the past cases most similar to this one and hand them to
        every agent as precedent. The audit log records which cases were
        recalled (ids and similarity, not their content), so it is always
        possible to see what informed a decision. Nothing is recalled - and
        nothing is logged - when memory is empty, disabled or unavailable.
        Returns the context handed to the agents ("" if nothing was recalled)."""
        hits = retrieve_similar_cases(use_case)
        context = format_memory_context(hits)
        self._apply_memory_context(context)
        if hits:
            log_event(
                use_case.id,
                "memory_retrieval",
                "agent_memory",
                {
                    "cases": [
                        {"use_case_id": hit.use_case_id, "similarity": round(hit.similarity, 4)}
                        for hit in hits
                    ]
                },
            )
        return context

    def _review_and_revise(
        self,
        use_case: AIUseCase,
        risk: RiskAssessmentResult,
        compliance: PolicyComplianceResult,
        decision: DecisionResult,
    ) -> DecisionResult:
        """Decision -> Review -> (revise Decision -> Review)* loop.

        Every review and every revised decision is written to the audit
        log. If the reviewer is still unsatisfied once the revision budget
        is spent, an `approve` is escalated to human approval - a decision
        the reviewer could not sign off on must not auto-complete. A
        `block` or `require_human_approval` needs no escalation: neither
        lets the use case go live without a person.
        """
        decision_history = [decision]
        review = self._run_stage(
            use_case, "review", self.review_agent,
            lambda: self.review_agent.review(use_case, risk, compliance, decision),
        )

        revisions = 0
        while (
            review.verdict == ReviewVerdict.NEEDS_REVISION
            and revisions < config.MAX_REVIEW_REVISIONS
        ):
            revisions += 1
            decision = self._run_stage(
                use_case, "decision_revision", self.decision_agent,
                lambda: self.decision_agent.decide(
                    use_case, risk, compliance, previous_decision=decision, review=review
                ),
            )
            decision_history.append(decision)
            review = self._run_stage(
                use_case, "review", self.review_agent,
                lambda: self.review_agent.review(use_case, risk, compliance, decision),
            )

        return self._settle_review(use_case, risk, compliance, decision, decision_history, review)

    def _settle_review(
        self,
        use_case: AIUseCase,
        risk: RiskAssessmentResult,
        compliance: PolicyComplianceResult,
        decision: DecisionResult,
        decision_history: List[DecisionResult],
        review: ReviewResult,
    ) -> DecisionResult:
        """What happens once the review loop is over (shared by `run` and the
        step-by-step flow): escalate an `approve` the reviewer never signed
        off on, then log the coordination diagnosis."""
        # The loop only ends with an unresolved review because the revision
        # budget ran out (an approving verdict ends it on its own), so this
        # is the "out of budget" signal for diagnostics.
        hit_revision_limit = review.verdict == ReviewVerdict.NEEDS_REVISION

        if hit_revision_limit and decision.decision == Decision.APPROVE:
            decision = _escalate_unresolved_review(decision, review)
            log_event(use_case.id, "review_escalation", "system", decision.model_dump(mode="json"))

        self._log_coordination_diagnosis(
            use_case, risk, compliance, decision, decision_history, review, hit_revision_limit
        )
        return decision

    def _log_coordination_diagnosis(
        self,
        use_case: AIUseCase,
        risk: RiskAssessmentResult,
        compliance: PolicyComplianceResult,
        decision: DecisionResult,
        decision_history: list,
        final_review: ReviewResult,
        hit_revision_limit: bool,
    ) -> None:
        """Deterministic, structural check for multi-agent coordination
        failures (conflicting conclusions, redundant/non-converging
        revisions, self-inconsistent findings) - see
        app.tools.coordination_diagnostics. Purely informational: nothing
        here changes the decision or report status, it only logs findings
        for a human reviewer to see."""
        issues = diagnose_coordination(
            risk, compliance, decision, decision_history, final_review, hit_revision_limit
        )
        if issues:
            log_event(
                use_case.id,
                "coordination_diagnosis",
                "system",
                {"issues": [issue.model_dump() for issue in issues]},
            )

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

    # ------------------------------------------------------------------
    # Step-by-step run: a person approves or rejects every agent's output
    # before the next agent starts.
    #
    #   start_run       -> Risk Assessment            (pending approval)
    #   decide_step(ok) -> Policy Compliance          (pending approval)
    #   decide_step(ok) -> Decision                   (pending approval)
    #   decide_step(ok) -> Review                     (pending approval)
    #   decide_step(ok) -> Decision (revision) -> Review -> ...  while the
    #                      reviewer asks for one and revisions are left
    #   decide_step(ok) -> GovernanceReport, exactly as `run` would end
    #   decide_step(no) -> run stops; nothing after that step runs
    #
    # The run is paused between requests (app.pipeline_runs), so each call
    # runs at most one agent. The audit log gets the same per-agent entries
    # as `run`, plus a `step_approval` entry for every human verdict.
    # ------------------------------------------------------------------

    def start_run(self, use_case: AIUseCase, created_by: Optional[str] = None) -> PipelineRun:
        """Begin a step-by-step run: run the Risk Assessment Agent and pause
        for a person to approve or reject its output."""
        save_use_case(use_case, created_by=created_by)
        log_event(use_case.id, "intake", "system", use_case.model_dump(mode="json"))

        run = PipelineRun(
            use_case=use_case,
            status=PipelineRunStatus.AWAITING_STEP_APPROVAL,
            memory_context=self._recall_similar_cases(use_case),
        )
        self._run_step(run, PipelineAgent.RISK_ASSESSMENT, revision=0)
        return run

    def decide_step(
        self,
        use_case_id: str,
        seq: int,
        approved: bool,
        approver: str,
        notes: Optional[str] = None,
    ) -> PipelineRun:
        """Approve or reject step `seq`, the one awaiting a decision.

        `seq` is required so a stale request (a double click, a second
        approver, an old tab) can't approve a step the person hasn't seen.
        Approving runs the next agent (or, after the last step, finalizes the
        report); rejecting stops the run. If the next agent fails, nothing is
        recorded - the step stays pending and can simply be approved again.

        Raises:
            UseCaseNotFoundError: no step-by-step run for this use case.
            InvalidStepStateError: the run isn't awaiting approval, or `seq`
                isn't the step awaiting it.
        """
        run = load_run(use_case_id)
        if run is None:
            raise UseCaseNotFoundError(use_case_id)
        if run.status != PipelineRunStatus.AWAITING_STEP_APPROVAL:
            raise InvalidStepStateError(
                f"Use case {use_case_id} is not awaiting step approval (status={run.status.value})"
            )
        step = run.steps[-1]
        if step.seq != seq or step.status != StepStatus.PENDING:
            raise InvalidStepStateError(
                f"Step {seq} is not awaiting approval; step {step.seq} "
                f"({step.agent.value}) is."
            )

        step.status = StepStatus.APPROVED if approved else StepStatus.REJECTED
        step.decided_by = approver
        step.notes = notes
        step.decided_at = utcnow_iso()

        if not approved:
            run.status = PipelineRunStatus.REJECTED
            save_run(run)
            self._log_step_decision(run, step)
            return run

        upcoming = self._next_agent(run)
        if upcoming is None:
            self._log_step_decision(run, step)
            run.report = self._complete_run(run)
            run.status = PipelineRunStatus.COMPLETED
            save_run(run)
            return run

        self._run_step(run, *upcoming, decided=step)
        return run

    @staticmethod
    def _next_agent(run: PipelineRun) -> Optional[Tuple[PipelineAgent, int]]:
        """The agent (and revision round) that follows the run's last step,
        or None if the run is over. Mirrors `_review_and_revise`."""
        last = run.steps[-1]
        if last.agent == PipelineAgent.RISK_ASSESSMENT:
            return PipelineAgent.POLICY_COMPLIANCE, 0
        if last.agent == PipelineAgent.POLICY_COMPLIANCE:
            return PipelineAgent.DECISION, 0
        if last.agent == PipelineAgent.DECISION:
            return PipelineAgent.REVIEW, last.revision

        review = ReviewResult.model_validate(last.output)
        if review.verdict == ReviewVerdict.NEEDS_REVISION and last.revision < config.MAX_REVIEW_REVISIONS:
            return PipelineAgent.DECISION, last.revision + 1
        return None

    def _run_step(
        self,
        run: PipelineRun,
        agent: PipelineAgent,
        revision: int,
        decided: Optional[PipelineStep] = None,
    ) -> None:
        """Run `agent`, add its output to the run as a pending step and save.

        The agent runs before anything is saved, so a failure leaves the run
        as it was. `decided` is the step whose approval led here; its
        approval is logged just ahead of this agent's own audit entry."""
        self._apply_memory_context(run.memory_context)
        output, stage, runner = self._call_agent(run, agent, revision)

        run.steps.append(
            PipelineStep(
                seq=len(run.steps) + 1,
                agent=agent,
                revision=revision,
                output=output.model_dump(mode="json"),
            )
        )
        save_run(run)
        if decided is not None:
            self._log_step_decision(run, decided)
        entry = log_event(run.use_case.id, stage, runner.name, output.model_dump(mode="json"))
        record_agent_run(runner, stage=stage, use_case_id=run.use_case.id, audit_log_id=entry.id)

    def _call_agent(
        self, run: PipelineRun, agent: PipelineAgent, revision: int
    ) -> Tuple[BaseModel, str, Any]:
        """Run one agent on the outputs of the steps before it. Returns its
        result with the audit-log stage `run` uses for it and the agent
        instance itself (its name is the audit actor; its trace is recorded)."""
        use_case = run.use_case
        if agent == PipelineAgent.RISK_ASSESSMENT:
            return self.risk_agent.assess(use_case), "risk_assessment", self.risk_agent

        risk = self._latest_output(run, PipelineAgent.RISK_ASSESSMENT, RiskAssessmentResult)
        if agent == PipelineAgent.POLICY_COMPLIANCE:
            result = self.policy_agent.check(use_case, risk)
            return result, "policy_compliance", self.policy_agent

        compliance = self._latest_output(run, PipelineAgent.POLICY_COMPLIANCE, PolicyComplianceResult)
        if agent == PipelineAgent.DECISION:
            if revision == 0:
                result = self.decision_agent.decide(use_case, risk, compliance)
                return result, "decision", self.decision_agent
            result = self.decision_agent.decide(
                use_case,
                risk,
                compliance,
                previous_decision=self._latest_output(run, PipelineAgent.DECISION, DecisionResult),
                review=self._latest_output(run, PipelineAgent.REVIEW, ReviewResult),
            )
            return result, "decision_revision", self.decision_agent

        decision = self._latest_output(run, PipelineAgent.DECISION, DecisionResult)
        result = self.review_agent.review(use_case, risk, compliance, decision)
        return result, "review", self.review_agent

    @staticmethod
    def _latest_output(
        run: PipelineRun, agent: PipelineAgent, model: Type[_OutputModel]
    ) -> _OutputModel:
        step = next(s for s in reversed(run.steps) if s.agent == agent)
        return model.model_validate(step.output)

    def _complete_run(self, run: PipelineRun) -> GovernanceReport:
        """All steps are approved and the reviewer is done: settle the review
        and write the report, exactly as the end of `run` does."""
        risk = self._latest_output(run, PipelineAgent.RISK_ASSESSMENT, RiskAssessmentResult)
        compliance = self._latest_output(run, PipelineAgent.POLICY_COMPLIANCE, PolicyComplianceResult)
        review = self._latest_output(run, PipelineAgent.REVIEW, ReviewResult)
        decision_history = [
            DecisionResult.model_validate(s.output)
            for s in run.steps
            if s.agent == PipelineAgent.DECISION
        ]
        decision = self._settle_review(
            run.use_case, risk, compliance, decision_history[-1], decision_history, review
        )
        return self._finalize(run.use_case, risk, compliance, decision)

    @staticmethod
    def _log_step_decision(run: PipelineRun, step: PipelineStep) -> None:
        log_event(
            run.use_case.id,
            "step_approval",
            step.decided_by,
            {
                "step": step.seq,
                "agent": step.agent.value,
                "revision": step.revision,
                "approved": step.status == StepStatus.APPROVED,
                "notes": step.notes,
            },
        )

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
        update_case_outcome(report)
        return report
