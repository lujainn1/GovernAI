"""Risk Assessment Agent.

Evaluates an AI use case and assigns a risk level (low/medium/high/critical)
with a numeric score and named risk factors. Two things happen deterministically
(without an LLM) before the model sees anything:

1. A keyword/weight baseline score is computed from the risk_rules table
   (see app.tools.risk_rules.get_baseline_risk_score) and handed to the model
   as an anchor - reproducible for the same input regardless of model
   variance, so the agent isn't reasoning from a blank page every time.
2. The submitted documentation is scanned for PII indicators and sensitive
   topics (app.tools.document_analysis.analyze_document).

Both tools also stay available for the model to call again on text of its
own choosing. After the model answers, the result's risk_level is
reconciled against its own risk_score using the organization's scoring
bands, so a governance decision can never contradict its own rubric.
"""
from typing import Optional

from app.agents.base import BaseAgent
from app.models import AIUseCase, RiskAssessmentResult, RiskLevel
from app.prompts import build_deterministic_signals, build_use_case_brief
from app.tools.document_analysis import ANALYZE_DOCUMENT_SCHEMA, analyze_document
from app.tools.risk_rules import (
    GET_BASELINE_RISK_SCORE_SCHEMA,
    GET_RISK_RULES_SCHEMA,
    GET_SCORING_BANDS_SCHEMA,
    get_baseline_risk_score,
    get_risk_rules,
    get_scoring_bands,
    score_to_level,
)

SYSTEM_PROMPT = """You are the Risk Assessment Agent inside a Multi-Agent AI \
Governance Platform. Your job is to evaluate a submitted AI use case (or AI \
agent) and assign it an overall risk level and numeric risk score (0-100).

The user message includes a "Deterministic Pre-Analysis" block the platform \
already computed for you: a reproducible baseline score from keyword/weight \
matching against the organization's risk rules, plus a scan of the \
documentation for PII indicators and sensitive-topic keywords. Treat this as \
a floor and a sanity check, not the final answer - it only catches rules \
with explicit keywords, so raise the score using your own judgment when the \
description implies risk the keywords miss (e.g. a consequential decision \
described without ever using the word "employment").

You can still call get_risk_rules to review the full rule set (including \
rules with no keywords, which need contextual judgment, not pattern \
matching), get_scoring_bands to see the exact score -> level thresholds, \
get_baseline_risk_score to re-run the keyword pass over any text of your \
choosing, and analyze_document for more detail on the raw documentation.

Weigh factors such as: sensitivity of data processed, level of autonomy, \
whether the system materially affects individuals (employment, credit, \
healthcare, legal rights), external/customer exposure, third-party \
dependencies, system permissions (can it write/delete/transfer/execute), \
and scale of deployment. Use the rule weights as guidance, not a rigid \
formula - use judgment for factors the rules don't cover.

List concrete risk_factors (short phrases) that drove your assessment, and \
give a clear rationale explaining the level you assigned. Your risk_score \
and risk_level must be internally consistent with the organization's \
scoring bands - the platform will otherwise auto-correct risk_level to \
match your score, so make sure the score itself reflects your true \
assessment rather than picking a level first and backfilling a number."""


class RiskAssessmentAgent(BaseAgent):
    name = "risk_assessment_agent"

    def __init__(self, model: Optional[str] = None):
        super().__init__(
            system_prompt=SYSTEM_PROMPT,
            tools=[
                GET_RISK_RULES_SCHEMA,
                GET_SCORING_BANDS_SCHEMA,
                GET_BASELINE_RISK_SCORE_SCHEMA,
                ANALYZE_DOCUMENT_SCHEMA,
            ],
            tool_functions={
                "get_risk_rules": get_risk_rules,
                "get_scoring_bands": get_scoring_bands,
                "get_baseline_risk_score": get_baseline_risk_score,
                "analyze_document": analyze_document,
            },
            model=model,
        )

    def assess(self, use_case: AIUseCase) -> RiskAssessmentResult:
        corpus = _use_case_corpus(use_case)
        baseline = get_baseline_risk_score(corpus)
        doc_analysis = analyze_document(use_case.documentation or "")

        message = (
            f"{build_use_case_brief(use_case)}\n\n"
            f"{build_deterministic_signals(baseline, doc_analysis)}"
        )
        result = self.run(message, RiskAssessmentResult)
        return _reconcile_with_bands(result)


def _use_case_corpus(use_case: AIUseCase) -> str:
    """Concatenate a use case's free-text fields for keyword scanning."""
    return "\n".join(
        filter(
            None,
            [
                use_case.name,
                use_case.description,
                use_case.data_classification,
                use_case.deployment_context,
                use_case.autonomy_level,
                use_case.documentation,
            ],
        )
    )


def _reconcile_with_bands(result: RiskAssessmentResult) -> RiskAssessmentResult:
    """Ensure risk_level agrees with risk_score under the org's scoring
    bands. The model is instructed to keep them consistent already, but LLMs
    occasionally pick a level that doesn't match their own score - and a
    governance decision that contradicts its own scoring rubric is worse
    than a clearly-labeled, deterministic auto-correction."""
    expected_level = score_to_level(result.risk_score)
    if expected_level == result.risk_level.value:
        return result

    note = (
        f"\n\n(Note: risk_level auto-adjusted from '{result.risk_level.value}' "
        f"to '{expected_level}' to stay consistent with the organization's "
        f"scoring bands for a score of {result.risk_score}/100.)"
    )
    return result.model_copy(
        update={
            "risk_level": RiskLevel(expected_level),
            "rationale": result.rationale + note,
        }
    )
