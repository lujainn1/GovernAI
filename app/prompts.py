"""Shared prompt-building helpers."""
from typing import Any, Dict

from app.models import AIUseCase, PolicyComplianceResult, RiskAssessmentResult


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
