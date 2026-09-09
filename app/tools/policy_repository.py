"""Tool: read/write access to the organizational policy repository.

Backed by the Supabase `policies` table. Exposed to the Policy Compliance
Agent (and the Decision Agent) as an OpenAI-style callable tool.
"""

from typing import Any, Dict, List, Optional

from app import db

TABLE = "policies"

# Real columns on the `policies` table. Anything else on a policy dict (e.g.
# principle, module, lifecycle_phase, evidence_required, source_refs -
# fields only some governance frameworks use) is stored in the `metadata`
# jsonb column and merged back to the top level on read, so callers keep
# seeing the same flat shape the old policies.yaml entries had.
_CORE_FIELDS = {
    "id",
    "title",
    "category",
    "description",
    "status",
    "coverage",
    "min_risk_level",
    "requires",
}


def _flatten(row: Dict[str, Any]) -> Dict[str, Any]:
    metadata = row.pop("metadata", None) or {}
    return {**metadata, **row}


def add_policy(policy: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a new policy to the `policies` table."""

    core = {k: v for k, v in policy.items() if k in _CORE_FIELDS}
    metadata = {k: v for k, v in policy.items() if k not in _CORE_FIELDS and k != "id"}
    row = {**core, "metadata": metadata} if metadata else core

    created = db.insert(TABLE, row)
    return _flatten(created)


def get_policies(
    category: Optional[str] = None,
    min_risk_level: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return organizational policies, optionally filtered.

    Args:
        category: policy category to filter by
            (e.g. data_privacy, security, human_oversight,
            third_party_data, transparency, fairness).

        min_risk_level: only return policies whose min_risk_level
            is at or below this level
            (low < medium < high < critical), i.e. policies
            that apply once a use case reaches this risk level.
    """

    order = {
        "low": 0,
        "medium": 1,
        "high": 2,
        "critical": 3,
    }

    params: Dict[str, Any] = {"order": "id.asc"}
    if category:
        params["category"] = f"eq.{category}"

    policies = [_flatten(dict(row)) for row in db.select(TABLE, params)]

    if min_risk_level and min_risk_level in order:
        threshold = order[min_risk_level]

        policies = [
            p
            for p in policies
            if order.get(
                p.get("min_risk_level", "low"),
                0,
            )
            <= threshold
        ]

    return policies


def search_policies(
    query: str,
) -> List[Dict[str, Any]]:
    """Keyword search over policy titles and descriptions."""

    q = query.lower()

    return [
        p
        for p in get_policies()
        if q in p.get("title", "").lower()
        or q in p.get("description", "").lower()
    ]


GET_POLICIES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_policies",
        "description": (
            "Retrieve organizational AI governance policies "
            "from the policy repository, optionally filtered "
            "by category and/or the risk level they start "
            "applying at."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "Optional category filter: "
                        "data_privacy, security, "
                        "human_oversight, third_party_data, "
                        "transparency, or fairness."
                    ),
                },
                "min_risk_level": {
                    "type": "string",
                    "enum": [
                        "low",
                        "medium",
                        "high",
                        "critical",
                    ],
                    "description": (
                        "Only return policies that apply "
                        "at or below this risk level."
                    ),
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
        "description": (
            "Keyword search organizational policies "
            "by title/description text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Keyword or phrase to search for."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}
