"""Shared prompt-building helpers."""
from app.models import AIUseCase, PolicyComplianceResult, RiskAssessmentResult


def build_use_case_brief(use_case: AIUseCase) -> str:
    return f"""AI Use Case Submission
=======================
ID: {use_case.id}
Name: {use_case.name}
Owner: {use_case.owner}
Description: {use_case.description}

Model provider: {use_case.model_provider or "unspecified"}
Model name: {use_case.model_name or "unspecified"}
Data sources: {", ".join(use_case.data_sources) or "none listed"}
Data classification: {use_case.data_classification or "unspecified"}
Permissions granted to the system: {", ".join(use_case.permissions) or "none listed"}
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
