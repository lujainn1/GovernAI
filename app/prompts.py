"""Shared prompt-building helpers."""
from typing import Any, Dict

from app.models import (
    AIUseCase,
    DecisionResult,
    PolicyComplianceResult,
    ReviewResult,
    RiskAssessmentResult,
)


def build_use_case_brief(use_case: AIUseCase) -> str:
    return f"""AI Use Case Submission
=======================
ID: {use_case.id}
Name: {use_case.name}
Owner: {use_case.owner}
Description: {use_case.description}

Data classification: {use_case.data_classification or "unspecified"}
Deployment context: {use_case.deployment_context or "unspecified"}
Autonomy level: {use_case.autonomy_level or "unspecified"}

Supporting documentation:
{use_case.documentation or "(none provided)"}
"""


def build_risk_context(risk: RiskAssessmentResult) -> str:
    return f"""Risk Assessment (already completed)
====================================
Risk level: {risk.risk_level.value}
Risk score: {risk.risk_score}/100
Risk factors: {", ".join(risk.risk_factors) or "none identified"}
Rationale: {risk.rationale}
"""


def build_compliance_context(compliance: PolicyComplianceResult) -> str:
    return f"""Policy Compliance Check (already completed)
=============================================
Status: {compliance.status.value}
Violated policies: {", ".join(compliance.violated_policies) or "none"}
Satisfied policies: {", ".join(compliance.satisfied_policies) or "none"}
Rationale: {compliance.rationale}
"""


def build_decision_context(decision: DecisionResult) -> str:
    return f"""Decision (to be reviewed)
==========================
Decision: {decision.decision.value}
Conditions: {"; ".join(decision.conditions) or "none"}
Rationale: {decision.rationale}
"""


def build_review_feedback_context(previous: DecisionResult, review: ReviewResult) -> str:
    """Feedback block appended to the Decision Agent's input on a revision
    pass: its own previous answer plus what the Review Agent objected to."""
    issues = "\n".join(f"- {i}" for i in review.issues) or "- (none listed)"
    suggestions = "\n".join(f"- {s}" for s in review.suggestions) or "- (none listed)"
    return f"""Reviewer Feedback - revise your previous decision
==================================================
Your previous decision was reviewed and sent back for revision.

{build_decision_context(previous)}
Issues the reviewer found:
{issues}

Suggested improvements:
{suggestions}

Reviewer's rationale: {review.rationale}

Produce a revised decision that addresses these issues. Keep what was \
already correct; only change the decision, conditions, or rationale where \
the feedback shows a real problem. If you disagree with a point, say why \
in your rationale rather than ignoring it.
"""


def build_deterministic_signals(baseline: Dict[str, Any], doc_analysis: Dict[str, Any]) -> str:
    matched = baseline.get("matched_rules") or []
    matched_lines = (
        "\n".join(
            f"- {m['id']} ({m['category']}, weight {m['weight']}): "
            f"matched keywords: {', '.join(m['matched_keywords'])}"
            for m in matched
        )
        or "(no keyword rules matched)"
    )
    sensitive_keywords = ", ".join(doc_analysis.get("sensitive_keywords_found") or []) or "none found"

    return f"""Deterministic Pre-Analysis (computed by the platform, not the model)
====================================================================
Baseline keyword/weight risk score: {baseline.get("suggested_score")}/100 \
(suggested level: {baseline.get("suggested_level")})
Matched risk rules:
{matched_lines}

Document scan - PII indicators present: {doc_analysis.get("contains_pii_indicators")}
Sensitive-topic keywords found in documentation: {sensitive_keywords}
"""
