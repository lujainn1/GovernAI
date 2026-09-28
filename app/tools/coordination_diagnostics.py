"""Tool: coordination-failure diagnostics for the governance pipeline.

Adapts the "Diagnosing Coordination Failures" lab
(Demo_Diagnosing_Coordination_Failures(Solution).ipynb) to this platform's
actual multi-agent pipeline (Risk -> Policy -> Decision -> Review, with a
revision loop; see app.orchestrator.GovernanceOrchestrator). That lab's demo
team of research agents shows four coordination problems - duplicated
research, conflicting conclusions, redundant work, and an inconsistent final
report - all traced to agents working without shared memory or a supervisor.

This module runs the same kind of after-the-fact diagnosis on a real
governance run, but as deterministic, structural checks over the agents' own
outputs and the review/revision history instead of an LLM call: a
coordination breakdown between two agents can be caught this way even when
each agent's individual output looks reasonable on its own.

Findings are informational only: GovernanceOrchestrator writes them to the
audit log (stage="coordination_diagnosis") for a human reviewer to see, and
they never change the decision or report status themselves.
"""
from typing import List, Sequence

from pydantic import BaseModel

from app.models import (
    ComplianceStatus,
    Decision,
    DecisionResult,
    PolicyComplianceResult,
    ReviewResult,
    ReviewVerdict,
    RiskAssessmentResult,
    RiskLevel,
)


class CoordinationIssue(BaseModel):
    code: str
    description: str


def _decision_signature(decision: DecisionResult) -> tuple:
    return (decision.decision, tuple(sorted(decision.conditions)))


def _check_conflicting_conclusions(
    risk: RiskAssessmentResult, compliance: PolicyComplianceResult, decision: DecisionResult
) -> List[CoordinationIssue]:
    """The lab's "conflicting conclusions" failure: agents downstream of each
    other end up disagreeing about the same use case."""
    issues = []
    if decision.decision == Decision.APPROVE and compliance.status == ComplianceStatus.NON_COMPLIANT:
        issues.append(
            CoordinationIssue(
                code="CONFLICTING_CONCLUSIONS",
                description=(
                    "Decision Agent approved a use case the Policy Compliance "
                    "Agent marked non-compliant."
                ),
            )
        )
    if decision.decision == Decision.APPROVE and risk.risk_level == RiskLevel.CRITICAL:
        issues.append(
            CoordinationIssue(
                code="CONFLICTING_CONCLUSIONS",
                description=(
                    "Decision Agent approved a use case the Risk Assessment "
                    "Agent rated critical risk."
                ),
            )
        )
    if (
        decision.decision == Decision.BLOCK
        and risk.risk_level == RiskLevel.LOW
        and compliance.status == ComplianceStatus.COMPLIANT
    ):
        issues.append(
            CoordinationIssue(
                code="CONFLICTING_CONCLUSIONS",
                description=(
                    "Decision Agent blocked a use case the Risk and Policy "
                    "agents found low-risk and compliant."
                ),
            )
        )
    return issues


def _check_self_inconsistent_compliance(compliance: PolicyComplianceResult) -> List[CoordinationIssue]:
    """The lab's "inconsistent final report" failure, applied one level
    down: a single agent's own output contradicts itself."""
    overlap = set(compliance.violated_policies) & set(compliance.satisfied_policies)
    if not overlap:
        return []
    policies = ", ".join(sorted(overlap))
    plural = "ies" if len(overlap) > 1 else "y"
    return [
        CoordinationIssue(
            code="INCONSISTENT_FINDINGS",
            description=(
                f"Policy Compliance Agent listed the same polic{plural} ({policies}) "
                "as both violated and satisfied."
            ),
        )
    ]


def _check_duplicate_risk_factors(risk: RiskAssessmentResult) -> List[CoordinationIssue]:
    """The lab's "duplicated research" failure: the same finding is listed
    more than once, as if produced by uncoordinated repeated passes."""
    factors = [f.strip().lower() for f in risk.risk_factors if f.strip()]
    if len(factors) == len(set(factors)):
        return []
    return [
        CoordinationIssue(
            code="REDUNDANT_WORK",
            description=(
                "Risk Assessment Agent listed duplicate risk factors, suggesting "
                "repeated/uncoordinated analysis."
            ),
        )
    ]


def _check_non_converging_revisions(decisions: Sequence[DecisionResult]) -> List[CoordinationIssue]:
    """The lab's "redundant work" failure: the Decision Agent re-submits the
    same answer after Review Agent feedback instead of acting on it, so the
    two agents make no progress toward agreement."""
    for previous, current in zip(decisions, decisions[1:]):
        if _decision_signature(previous) == _decision_signature(current):
            return [
                CoordinationIssue(
                    code="REDUNDANT_WORK",
                    description=(
                        "Decision Agent re-submitted an identical decision after Review "
                        "Agent feedback instead of revising it - the two agents made no "
                        "progress toward agreement."
                    ),
                )
            ]
    return []


def _check_unresolved_review_loop(
    final_review: ReviewResult, hit_revision_limit: bool
) -> List[CoordinationIssue]:
    """The lab's root cause, made explicit: without a supervisor or shared
    memory, Decision and Review can keep disagreeing until the revision
    budget simply runs out."""
    if hit_revision_limit and final_review.verdict == ReviewVerdict.NEEDS_REVISION:
        return [
            CoordinationIssue(
                code="UNRESOLVED_COORDINATION",
                description=(
                    "Review Agent still had unresolved issues after the maximum number "
                    "of revisions - Decision and Review agents could not converge."
                ),
            )
        ]
    return []


def diagnose_coordination(
    risk: RiskAssessmentResult,
    compliance: PolicyComplianceResult,
    decision: DecisionResult,
    decision_history: Sequence[DecisionResult],
    final_review: ReviewResult,
    hit_revision_limit: bool,
) -> List[CoordinationIssue]:
    """Run every structural coordination check against one completed run.

    Args:
        risk: the Risk Assessment Agent's result.
        compliance: the Policy Compliance Agent's result.
        decision: the final DecisionResult (after any revision/escalation).
        decision_history: every DecisionResult produced for this run, in
            order - the initial decision plus each revision.
        final_review: the last ReviewResult the Review Agent produced.
        hit_revision_limit: True when the revise/review loop ran out of
            budget while the Review Agent still wanted changes (see
            GovernanceOrchestrator._review_and_revise).
    """
    issues: List[CoordinationIssue] = []
    issues.extend(_check_conflicting_conclusions(risk, compliance, decision))
    issues.extend(_check_self_inconsistent_compliance(compliance))
    issues.extend(_check_duplicate_risk_factors(risk))
    issues.extend(_check_non_converging_revisions(decision_history))
    issues.extend(_check_unresolved_review_loop(final_review, hit_revision_limit))
    return issues
