"""Tool: read-only access to the organizational policy repository.

Backed by data/policies.yaml. Exposed to the Policy Compliance Agent (and
the Decision Agent) as an OpenAI-style callable tool.
"""
from functools import lru_cache
from typing import Any, Dict, List, Optional

import yaml

from app import config


@lru_cache(maxsize=1)
def _load_policies() -> List[Dict[str, Any]]:
    with open(config.POLICIES_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("policies", [])


def get_policies(category: Optional[str] = None, min_risk_level: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return organizational policies, optionally filtered.

    Args:
        category: policy category to filter by (e.g. data_privacy, security,
            human_oversight, third_party_data, transparency, fairness).
        min_risk_level: only return policies whose min_risk_level is at or
            below this level (low < medium < high < critical), i.e. policies
            that apply once a use case reaches this risk level.
    """
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    policies = _load_policies()

    if category:
        policies = [p for p in policies if p.get("category") == category]

    if min_risk_level and min_risk_level in order:
        threshold = order[min_risk_level]
        policies = [p for p in policies if order.get(p.get("min_risk_level", "low"), 0) <= threshold]

    return policies


def search_policies(query: str) -> List[Dict[str, Any]]:
    """Keyword search over policy titles and descriptions."""
    q = query.lower()
    return [
        p
        for p in _load_policies()
        if q in p.get("title", "").lower() or q in p.get("description", "").lower()
    ]


GET_POLICIES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_policies",
        "description": (
            "Retrieve organizational AI governance policies from the policy "
            "repository, optionally filtered by category and/or the risk "
            "level they start applying at."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "Optional category filter: data_privacy, security, "
                        "human_oversight, third_party_data, transparency, or fairness."
                    ),
                },
                "min_risk_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "Only return policies that apply at or below this risk level.",
                },
            },
            "required": [],
        },
    },
}

SEARCH_POLICIES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_policies",
        "description": "Keyword search organizational policies by title/description text.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword or phrase to search for."},
            },
            "required": ["query"],
        },
    },
}
