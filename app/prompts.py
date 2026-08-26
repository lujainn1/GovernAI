"""Shared prompt-building helpers."""
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
Deployment status: {use_case.deployment_status}

Control-routing flags:
- personal_data: {use_case.personal_data}
- sensitive_data: {use_case.sensitive_data}
- generative_ai: {use_case.generative_ai}
- external_provider: {use_case.external_provider}
- data_outside_ksa: {use_case.data_outside_ksa}
- high_impact_decision: {use_case.high_impact_decision}
- user_facing_chat: {use_case.user_facing_chat}
- research_purpose: {use_case.research_purpose}

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
Violated (FAIL) controls: {", ".join(compliance.violated_policies) or "none"}
Needs review controls: {", ".join(compliance.review_policies) or "none"}
Satisfied (PASS) controls: {", ".join(compliance.satisfied_policies) or "none"}
Rationale: {compliance.rationale}
"""
