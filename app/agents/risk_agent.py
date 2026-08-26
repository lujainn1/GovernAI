"""Risk Assessment Agent.

Risk detection and scoring are deterministic (app/tools/risk_engine.py,
per the SDAIA-derived risk library) - the LLM is used only to write the
narrative rationale and named risk_factors from the already-computed
register. It never sets the risk level or score itself.
"""
from typing import List, Optional

from pydantic import BaseModel, Field

from app.agents.base import BaseAgent
from app.models import AIUseCase, DetectedRisk, RiskAssessmentResult
from app.prompts import build_use_case_brief
from app.tools.document_analysis import ANALYZE_DOCUMENT_SCHEMA, analyze_document
from app.tools.risk_engine import compute_overall_risk, detect_risks
from app.tools.risk_rules import GET_RISK_RULES_SCHEMA, get_risk_rules


class _RiskNarrative(BaseModel):
    risk_factors: List[str] = Field(default_factory=list)
    rationale: str


SYSTEM_PROMPT = """You are the Risk Assessment Agent inside a Multi-Agent \
AI Governance Platform. A deterministic risk-detection engine has already \
identified which named risks apply to this use case (from the SDAIA-derived \
risk library) and computed the overall risk level and score - you do not \
set those numbers and must not contradict them.

Your job is to write a short list of risk_factors (concrete short phrases \
drawn from the detected risks) and a clear rationale paragraph explaining, \
in plain language, why the detected risks justify the assigned risk level. \
You may call get_risk_rules for background on a risk domain, or \
analyze_document on the submitted documentation to note supporting \
evidence in your rationale (e.g. PII indicators found)."""


class RiskAssessmentAgent(BaseAgent):
    name = "risk_assessment_agent"

    def __init__(self, model: Optional[str] = None):
        super().__init__(
            system_prompt=SYSTEM_PROMPT,
            tools=[GET_RISK_RULES_SCHEMA, ANALYZE_DOCUMENT_SCHEMA],
            tool_functions={
                "get_risk_rules": get_risk_rules,
                "analyze_document": analyze_document,
            },
            model=model,
        )

    def assess(self, use_case: AIUseCase) -> RiskAssessmentResult:
        detected_risks: List[DetectedRisk] = detect_risks(use_case)
        risk_level, risk_score = compute_overall_risk(detected_risks)

        register_brief = "\n".join(
            f"- {r.risk_id} ({r.domain}): {r.title} - likelihood {r.likelihood} x impact "
            f"{r.impact} = {r.inherent_score}/25. Treatment: {r.treatment}"
            for r in detected_risks
        ) or "No named risks were triggered by this submission."

        message = (
            f"{build_use_case_brief(use_case)}\n\n"
            f"Deterministically detected risk register (already scored - do not change):\n"
            f"{register_brief}\n\n"
            f"Overall risk level (fixed): {risk_level.value}\n"
            f"Overall risk score (fixed): {risk_score}/100"
        )

        narrative = self.run(message, _RiskNarrative)

        return RiskAssessmentResult(
            risk_level=risk_level,
            risk_score=risk_score,
            risk_factors=narrative.risk_factors or [r.title for r in detected_risks],
            rationale=narrative.rationale,
            detected_risks=detected_risks,
        )
