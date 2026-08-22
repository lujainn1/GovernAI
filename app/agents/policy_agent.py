"""Policy Compliance Agent.

Checks a submitted AI use case (with its already-computed risk assessment)
against the organizational policy repository and reports which policies are
satisfied vs. violated.
"""
from typing import Optional

from app.agents.base import BaseAgent
from app.models import AIUseCase, PolicyComplianceResult, RiskAssessmentResult
from app.prompts import build_risk_context, build_use_case_brief
from app.tools.document_analysis import ANALYZE_DOCUMENT_SCHEMA, analyze_document
from app.tools.policy_repository import (
    GET_POLICIES_SCHEMA,
    SEARCH_POLICIES_SCHEMA,
    get_policies,
    search_policies,
)

SYSTEM_PROMPT = """You are the Policy Compliance Agent inside a Multi-Agent \
AI Governance Platform. You receive an AI use case and its risk assessment \
(already completed by the Risk Assessment Agent). Your job is to check the \
use case against the organization's AI governance policies and report \
compliance.

Call get_policies (you can filter by min_risk_level using the use case's \
risk level, and/or by category) to retrieve the policies that apply. Use \
search_policies if you want to look up something specific. You may call \
analyze_document on the use case's documentation if you need to verify a \
factual claim (e.g. whether PII appears to be present).

For each applicable policy, decide whether the use case, as described, \
satisfies the policy's "requires" items based on the information given. If \
information needed to confirm a requirement is simply missing from the \
submission (not explicitly stated as done), treat that policy as violated \
or partially met rather than assuming compliance - governance decisions must \
be conservative about unverified claims.

Return the policy IDs (e.g. "POL-003") in violated_policies and \
satisfied_policies, and a rationale explaining your findings."""


class PolicyComplianceAgent(BaseAgent):
    name = "policy_compliance_agent"

    def __init__(self, model: Optional[str] = None):
        super().__init__(
            system_prompt=SYSTEM_PROMPT,
            tools=[GET_POLICIES_SCHEMA, SEARCH_POLICIES_SCHEMA, ANALYZE_DOCUMENT_SCHEMA],
            tool_functions={
                "get_policies": get_policies,
                "search_policies": search_policies,
                "analyze_document": analyze_document,
            },
            model=model,
        )

    def check(self, use_case: AIUseCase, risk: RiskAssessmentResult) -> PolicyComplianceResult:
        message = f"{build_use_case_brief(use_case)}\n\n{build_risk_context(risk)}"
        return self.run(message, PolicyComplianceResult)
