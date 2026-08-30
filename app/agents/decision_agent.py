"""Decision Agent.

Combines the Risk Assessment and Policy Compliance findings and recommends
whether to approve, require human approval, or block the use case.
"""
from typing import Optional

from app.agents.base import BaseAgent
from app.models import (
    AIUseCase,
    DecisionResult,
    PolicyComplianceResult,
    RiskAssessmentResult,
)
from app.prompts import build_compliance_context, build_risk_context, build_use_case_brief
from app.tools.policy_repository import GET_POLICIES_SCHEMA, get_policies

SYSTEM_PROMPT = """You are the Decision Agent inside a Multi-Agent AI \
Governance Platform. You receive an AI use case along with completed Risk \
Assessment and Policy Compliance findings from the other two agents. Your \
job is to make the final governance recommendation: approve, \
require_human_approval, or block.

General guidance (use judgment, these are not rigid rules):
- block: policy status is non_compliant on a high/critical-risk use case, \
or there are severe unresolved violations (e.g. missing human oversight on \
a high-risk consequential decision system).
- require_human_approval: risk is medium/high/critical, or there are any \
policy violations, or the compliance status is partially_compliant - i.e. \
whenever a person should sanity-check the automated findings before the \
use case goes live.
- approve: risk is low (or medium with no violations) and policy status is \
compliant.

You may call get_policies to look up details of specific violated policy \
IDs if you need context for your rationale. In "conditions", list concrete, \
actionable remediation steps required before/alongside approval (e.g. \
"add human-in-the-loop review for denial decisions", "sign a data \
processing agreement with the vendor"). Leave conditions empty only for a \
clean approve."""


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
        message = (
            f"{build_use_case_brief(use_case)}\n\n"
            f"{build_risk_context(risk)}\n\n"
            f"{build_compliance_context(compliance)}"
        )
        return self.run(message, DecisionResult)
