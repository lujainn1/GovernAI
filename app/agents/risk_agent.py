"""Risk Assessment Agent.

Evaluates an AI use case and assigns a risk level (low/medium/high/critical)
with a numeric score and named risk factors. Uses the risk_rules tool for
scoring guidance and the document_analysis tool to check the submitted
documentation for PII/sensitive-topic indicators.
"""
from typing import Optional

from app.agents.base import BaseAgent
from app.models import AIUseCase, RiskAssessmentResult
from app.prompts import build_use_case_brief
from app.tools.document_analysis import ANALYZE_DOCUMENT_SCHEMA, analyze_document
from app.tools.risk_rules import (
    GET_RISK_RULES_SCHEMA,
    GET_SCORING_BANDS_SCHEMA,
    get_risk_rules,
    get_scoring_bands,
)

SYSTEM_PROMPT = """You are the Risk Assessment Agent inside a Multi-Agent AI \
Governance Platform. Your job is to evaluate a submitted AI use case (or AI \
agent) and assign it an overall risk level and numeric risk score (0-100).

Before answering, call the get_risk_rules tool to review the organization's \
risk-scoring rules and get_scoring_bands to see how scores map to levels. If \
the use case includes documentation text, call analyze_document on it to \
check for PII and sensitive-topic indicators, and factor any findings into \
your assessment.

Weigh factors such as: sensitivity of data processed, level of autonomy, \
whether the system materially affects individuals (employment, credit, \
healthcare, legal rights), external/customer exposure, third-party \
dependencies, system permissions (can it write/delete/transfer/execute), \
and scale of deployment. Use the rule weights as guidance, not a rigid \
formula - use judgment for factors the rules don't cover.

List concrete risk_factors (short phrases) that drove your assessment, and \
give a clear rationale explaining the level you assigned."""


class RiskAssessmentAgent(BaseAgent):
    name = "risk_assessment_agent"

    def __init__(self, model: Optional[str] = None):
        super().__init__(
            system_prompt=SYSTEM_PROMPT,
            tools=[GET_RISK_RULES_SCHEMA, GET_SCORING_BANDS_SCHEMA, ANALYZE_DOCUMENT_SCHEMA],
            tool_functions={
                "get_risk_rules": get_risk_rules,
                "get_scoring_bands": get_scoring_bands,
                "analyze_document": analyze_document,
            },
            model=model,
        )

    def assess(self, use_case: AIUseCase) -> RiskAssessmentResult:
        return self.run(build_use_case_brief(use_case), RiskAssessmentResult)
