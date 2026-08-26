"""Deterministic control router.

Implements Part B5/D3 of the SDAIA-derived governance library: decides
which controls from data/policies.yaml apply to a submitted use case,
based on its routing fields - "which policies are relevant" is not left
to the LLM's judgment.

AI_ETHICS controls for the pre-deployment lifecycle phases (plan_design,
prepare_data, build_validate) always apply; its deploy_monitor controls
only apply once the use case is actually in production, matching the
library's "AI-ETH:* (all lifecycle controls relevant to current phase)"
routing rule. PDPL/XFER/GEN controls apply only when every condition in
their applies_when list is true.
"""
from typing import Any, Callable, Dict, List

from app.models import AIUseCase
from app.tools.policy_repository import _load_policies

_CONDITIONS: Dict[str, Callable[[AIUseCase], bool]] = {
    "personal_data": lambda uc: uc.personal_data,
    "sensitive_data": lambda uc: uc.sensitive_data,
    "generative_ai": lambda uc: uc.generative_ai,
    "external_provider": lambda uc: uc.external_provider,
    "data_outside_ksa": lambda uc: uc.data_outside_ksa,
    "high_impact_decision": lambda uc: uc.high_impact_decision,
    "user_facing_chat": lambda uc: uc.user_facing_chat,
    "research_purpose": lambda uc: uc.research_purpose,
    "deployment_status_production": lambda uc: uc.deployment_status == "production",
}

_PRE_DEPLOYMENT_PHASES = {"plan_design", "prepare_data", "build_validate"}


def route_controls(use_case: AIUseCase) -> List[Dict[str, Any]]:
    """Return the subset of controls that apply to this use case."""
    applicable: List[Dict[str, Any]] = []

    for control in _load_policies():
        if control.get("module") == "AI_ETHICS" and control.get("phase") in _PRE_DEPLOYMENT_PHASES:
            applicable.append(control)
            continue

        conditions = control.get("applies_when") or []
        if conditions and all(_CONDITIONS.get(cond, lambda uc: False)(use_case) for cond in conditions):
            applicable.append(control)

    return applicable
