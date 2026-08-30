"""Tool: read/write access to the risk-scoring rule set.

Backed by data/risk_rules.yaml. Exposed to the Risk Assessment Agent as an
OpenAI-style callable tool. The agent is expected to use these rules as
guidance for scoring; the platform does not force a purely mechanical score
so that the LLM can reason about context the keyword rules miss.
"""

from functools import lru_cache
from typing import Any, Dict, List, Optional

import yaml

from app import config


@lru_cache(maxsize=1)
def _load_risk_rules() -> Dict[str, Any]:
    with open(config.RISK_RULES_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def add_risk_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a new risk rule to data/risk_rules.yaml."""

    with open(config.RISK_RULES_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    rules = data.get("risk_rules", [])

    rule_id = rule.get("id")

    if rule_id and any(
        existing.get("id") == rule_id
        for existing in rules
    ):
        raise ValueError(
            f"Risk rule with id '{rule_id}' already exists"
        )

    rules.append(rule)
    data["risk_rules"] = rules

    with open(config.RISK_RULES_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            sort_keys=False,
            allow_unicode=True,
        )

    # Important: refresh cached rules immediately
    _load_risk_rules.cache_clear()

    return rule


def get_risk_rules(
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return risk-scoring rules, optionally filtered by category.

    Args:
        category: optional category filter
            (e.g. data_sensitivity, autonomy,
            consequential_decisions, external_exposure,
            third_party_dependency, system_access, scale).
    """

    rules = _load_risk_rules().get(
        "risk_rules",
        [],
    )

    if category:
        rules = [
            r
            for r in rules
            if r.get("category") == category
        ]

    return rules


def get_scoring_bands() -> List[Dict[str, Any]]:
    """Return score->risk-level bands used to translate a numeric score."""

    return (
        _load_risk_rules()
        .get("scoring", {})
        .get("bands", [])
    )


GET_RISK_RULES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_risk_rules",
        "description": (
            "Retrieve the organization's risk-scoring rules "
            "used to evaluate AI use cases, optionally "
            "filtered by category."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "Optional category filter: "
                        "data_sensitivity, autonomy, "
                        "consequential_decisions, "
                        "external_exposure, "
                        "third_party_dependency, "
                        "system_access, or scale."
                    ),
                },
            },
            "required": [],
        },
    },
}


GET_SCORING_BANDS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_scoring_bands",
        "description": (
            "Retrieve the numeric score ranges that map "
            "to low/medium/high/critical risk levels."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}