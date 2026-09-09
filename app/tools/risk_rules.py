"""Tool: read/write access to the risk-scoring rule set.

Backed by the Supabase `risk_rules` table. Exposed to the Risk Assessment
Agent as an OpenAI-style callable tool. The agent is expected to use these
rules as guidance for scoring; the platform does not force a purely
mechanical score so that the LLM can reason about context the keyword rules
miss.
"""

from typing import Any, Dict, List, Optional

from app import db

TABLE = "risk_rules"

# Real columns on the `risk_rules` table. Anything else on a rule dict is
# stored in the `metadata` jsonb column and merged back to the top level on
# read - the risk rule set mixes several styles of rule (keyword-weighted,
# condition/action, domain/treatment) that don't share every field.
_CORE_FIELDS = {
    "id",
    "title",
    "category",
    "domain",
    "severity",
    "description",
    "condition",
    "action",
    "treatment",
    "weight",
    "trigger_keywords",
    "source_refs",
}

# Score -> risk-level bands. Fixed thresholds tied to how risk_score (0-100)
# is interpreted, not organizational data reviewers edit, so unlike policies
# and risk rules these aren't a table.
SCORING_BANDS = [
    {"level": "low", "max_score": 24},
    {"level": "medium", "max_score": 49},
    {"level": "high", "max_score": 74},
    {"level": "critical", "max_score": 100},
]


def _flatten(row: Dict[str, Any]) -> Dict[str, Any]:
    metadata = row.pop("metadata", None) or {}
    return {**metadata, **row}


def add_risk_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a new risk rule to the `risk_rules` table."""

    core = {k: v for k, v in rule.items() if k in _CORE_FIELDS}
    metadata = {k: v for k, v in rule.items() if k not in _CORE_FIELDS and k != "id"}
    row = {**core, "metadata": metadata} if metadata else core

    created = db.insert(TABLE, row)
    return _flatten(created)


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

    params: Dict[str, Any] = {"order": "id.asc"}
    if category:
        params["category"] = f"eq.{category}"

    return [_flatten(dict(row)) for row in db.select(TABLE, params)]


def get_scoring_bands() -> List[Dict[str, Any]]:
    """Return score->risk-level bands used to translate a numeric score."""

    return SCORING_BANDS


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
