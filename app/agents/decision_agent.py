"""Decision Agent.

The final approve / require_human_approval / block verdict is computed
deterministically (see deterministic_decision below, Part C3 of the
SDAIA-derived library) from the risk level and control evaluations - the
LLM only explains the result and lists remediation conditions, it does
not choose the verdict.
"""
from typing import List, Optional

from pydantic import BaseModel, Field

from app.agents.base import BaseAgent
from app.models import (
    AIUseCase,
    ControlResultStatus,
    Decision,
    DecisionResult,
    PolicyComplianceResult,
    RiskAssessmentResult,
    RiskLevel,
)
from app.prompts import build_compliance_context, build_risk_context, build_use_case_brief
from app.tools.policy_repository import GET_POLICIES_SCHEMA, get_policies


class _DecisionNarrative(BaseModel):
    conditions: List[str] = Field(default_factory=list)
    rationale: str


SYSTEM_PROMPT = """You are the Decision Agent inside a Multi-Agent AI \
Governance Platform. The final approve / require_human_approval / block \
verdict has already been computed deterministically from the risk level \
and control evaluations (per the SDAIA-derived library's decision rule) - \
you do not choose it and must not contradict it.

Your job is to write "conditions": concrete, actionable remediation steps \
required before/alongside approval (e.g. "add human-in-the-loop review for \
denial decisions", "sign a data processing agreement with the vendor", \
"complete the DPIA"), grounded in the specific failed/review controls \
listed. Write a "rationale" paragraph explaining, given the risk level and \
those controls, why this outcome is warranted. Leave conditions empty only \
when there are no failed or review-state controls. You may call \
get_policies for a control's full text if you need it for your rationale."""


def deterministic_decision(risk_level: RiskLevel, compliance: PolicyComplianceResult) -> Decision:
    """Part C3 decision rule: high/critical risk or failed critical
    controls drive block or human review; any unresolved finding requires
    human review; a clean record at acceptable risk auto-approves."""
    has_critical_fail = any(
        e.critical and e.result == ControlResultStatus.FAIL for e in compliance.control_evaluations
    )
    has_finding = bool(compliance.violated_policies or compliance.review_policies)

    if risk_level == RiskLevel.CRITICAL:
        return Decision.BLOCK
    if risk_level == RiskLevel.HIGH and has_critical_fail:
        return Decision.BLOCK
    if has_critical_fail or has_finding or risk_level == RiskLevel.HIGH:
        return Decision.REQUIRE_HUMAN_APPROVAL
    return Decision.APPROVE


class DecisionAgent(BaseAgent):
    name = "decision_agent"

    def __init__(self, model: Optional[str] = None):
        super().__init__(
            system_prompt=SYSTEM_PROMPT,
            tools=[GET_POLICIES_SCHEMA],
            tool_functions={"get_policies": get_policies},
            model=model,
        )

    def decide(
        self,
        use_case: AIUseCase,
        risk: RiskAssessmentResult,
        compliance: PolicyComplianceResult,
    ) -> DecisionResult:
        decision = deterministic_decision(risk.risk_level, compliance)

        message = (
            f"{build_use_case_brief(use_case)}\n\n"
            f"{build_risk_context(risk)}\n\n"
            f"{build_compliance_context(compliance)}\n\n"
            f"Deterministically computed decision (fixed - explain it, do not change it): "
            f"{decision.value}"
        )

        narrative = self.run(message, _DecisionNarrative)

        return DecisionResult(
            decision=decision,
            conditions=narrative.conditions,
            rationale=narrative.rationale,
        )
