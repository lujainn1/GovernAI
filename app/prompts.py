"""Shared prompt-building helpers."""
from typing import Any, Dict, List

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


def build_evidence_context(candidates: List[Dict[str, Any]]) -> str:
    """Render the retrieved SDAIA passages for an agent prompt.

    Ids are the retrieval order (E1 first), which is deliberately not a
    statement about which passage is most relevant - the agent is asked to
    judge that itself.
    """
    if not candidates:
        return """SDAIA RETRIEVED EVIDENCE
========================
(no SDAIA passages were retrieved for this submission)
"""

    blocks = []

    for candidate in candidates:
        blocks.append(
            f"[{candidate['evidence_id']}]\n"
            f"Source: {candidate['source']}\n"
            f"Page: {candidate['page']}\n"
            f"Passage: {' '.join(str(candidate['text']).split())}"
        )

    listing = "\n\n".join(blocks)

    return f"""SDAIA RETRIEVED EVIDENCE
========================
{listing}
"""


def build_compliance_context(compliance: PolicyComplianceResult) -> str:
    """Render the compliance findings for the Decision and Review agents.

    `undetermined_policies` are applicable policies the submission did not
    settle either way. They are listed separately from violations on purpose:
    they are open information gaps to be resolved (by conditions or by a
    human), not breaches.
    """
    return f"""Policy Compliance Check (already completed)
=============================================
Status: {compliance.status.value}
Violated policies (evidence of a conflict): {", ".join(compliance.violated_policies) or "none"}
Satisfied policies (evidence the requirement is met): {", ".join(compliance.satisfied_policies) or "none"}
Undetermined policies (applicable, but the submission does not show either way - information gaps, not violations): {", ".join(compliance.undetermined_policies) or "none"}
Not applicable to this use case: {", ".join(compliance.not_applicable_policies) or "none"}
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

    category_lines = (
        "\n".join(f"- {c['category']}: {c['weight']}" for c in baseline.get("category_breakdown") or [])
        or "(no categories matched)"
    )
    judgment_lines = (
        "\n".join(
            f"- {r['id']} ({r['category']}): {r['condition']}"
            for r in baseline.get("rules_requiring_judgment") or []
        )
        or "(none)"
    )

    return f"""Deterministic Pre-Analysis (computed by the platform, not the model)
====================================================================
Baseline keyword/weight risk score: {baseline.get("suggested_score")}/100 \
(suggested level: {baseline.get("suggested_level")})
Matched risk rules:
{matched_lines}

Score contribution by category (from the matched rules only - several rules \
can fire on the same underlying fact, so a high total may be one risk \
counted more than once rather than several distinct risks):
{category_lines}

Rules requiring contextual judgment (NOT reflected in the score above - they \
have no trigger_keywords, so they were never scanned; check each one against \
the use case yourself):
{judgment_lines}

Document scan - PII indicators present: {doc_analysis.get("contains_pii_indicators")}
Sensitive-topic keywords found in documentation: {sensitive_keywords}
"""
