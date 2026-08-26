"""Policy Compliance Agent.

Which controls apply is decided deterministically by the control router
(app/tools/control_router.py, per the SDAIA-derived control library) - the
LLM's job is only to evaluate each already-selected control as
pass/fail/review/not_applicable given the submitted facts.
"""
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.agents.base import BaseAgent
from app.models import (
    AIUseCase,
    ComplianceStatus,
    ControlEvaluation,
    ControlResultStatus,
    PolicyComplianceResult,
    RiskAssessmentResult,
)
from app.prompts import build_risk_context, build_use_case_brief
from app.tools.control_router import route_controls
from app.tools.document_analysis import ANALYZE_DOCUMENT_SCHEMA, analyze_document
from app.tools.policy_repository import SEARCH_POLICIES_SCHEMA, search_policies


class _ControlVerdict(BaseModel):
    control_id: str
    result: ControlResultStatus
    rationale: str = ""


class _ComplianceNarrative(BaseModel):
    status: ComplianceStatus
    verdicts: List[_ControlVerdict] = Field(default_factory=list)
    rationale: str


SYSTEM_PROMPT = """You are the Policy Compliance Agent inside a Multi-Agent \
AI Governance Platform. You receive an AI use case, its risk assessment, \
and a fixed list of controls that a deterministic router has already \
selected as applicable (from the SDAIA-derived control library) - do not \
add or remove controls from that list, and do not invent control IDs.

For every control_id in the provided list, return exactly one verdict: \
"pass" if the submission clearly satisfies it, "fail" if it clearly does \
not, or "review" if the submission doesn't give you enough information to \
tell. Be conservative: if evidence for a requirement is simply missing \
from the submission (not explicitly stated as done), use "review" or \
"fail" rather than assuming compliance - governance decisions must not \
assume unverified claims are true.

You may call search_policies to look up a control's full text, or \
analyze_document on the submitted documentation to check a factual claim \
(e.g. whether PII appears to be present). Set the overall status \
(compliant / partially_compliant / non_compliant) based on how many \
controls failed or need review, and write a rationale paragraph \
summarizing the key findings."""


class PolicyComplianceAgent(BaseAgent):
    name = "policy_compliance_agent"

    def __init__(self, model: Optional[str] = None):
        super().__init__(
            system_prompt=SYSTEM_PROMPT,
            tools=[SEARCH_POLICIES_SCHEMA, ANALYZE_DOCUMENT_SCHEMA],
            tool_functions={
                "search_policies": search_policies,
                "analyze_document": analyze_document,
            },
            model=model,
        )

    def check(self, use_case: AIUseCase, risk: RiskAssessmentResult) -> PolicyComplianceResult:
        routed = route_controls(use_case)

        control_brief = "\n".join(
            f"- {c['id']} [{'CRITICAL' if c.get('critical') else 'standard'}] "
            f"({c['module']}/{c['principle']}): {c['question']}"
            for c in routed
        ) or "No controls were routed for this use case."

        message = (
            f"{build_use_case_brief(use_case)}\n\n"
            f"{build_risk_context(risk)}\n\n"
            f"Applicable controls (routed deterministically - evaluate every one, "
            f"using exactly these control_id values):\n{control_brief}"
        )

        narrative = self.run(message, _ComplianceNarrative)

        by_id: Dict[str, dict] = {c["id"]: c for c in routed}
        verdict_by_id = {v.control_id: v for v in narrative.verdicts}

        evaluations: List[ControlEvaluation] = []
        violated: List[str] = []
        satisfied: List[str] = []
        review: List[str] = []

        for control_id, control in by_id.items():
            verdict = verdict_by_id.get(control_id)
            result = verdict.result if verdict else ControlResultStatus.REVIEW
            rationale = (
                verdict.rationale
                if verdict
                else "No verdict returned for this control; treated as needing review."
            )

            evaluations.append(
                ControlEvaluation(
                    control_id=control_id,
                    module=control["module"],
                    principle=control["principle"],
                    control=control["control"],
                    result=result,
                    critical=control.get("critical", False),
                    rationale=rationale,
                    evidence_required=control.get("evidence_required", []),
                    source_refs=control.get("source_refs", []),
                )
            )

            if result == ControlResultStatus.FAIL:
                violated.append(control_id)
            elif result == ControlResultStatus.PASS:
                satisfied.append(control_id)
            elif result == ControlResultStatus.REVIEW:
                review.append(control_id)

        return PolicyComplianceResult(
            status=narrative.status,
            violated_policies=violated,
            satisfied_policies=satisfied,
            review_policies=review,
            rationale=narrative.rationale,
            control_evaluations=evaluations,
        )
