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


def score_to_level(score: int, bands: Optional[List[Dict[str, Any]]] = None) -> str:
    """Map a 0-100 risk score to a level using the scoring bands.

    Args:
        score: the numeric risk score (0-100).
        bands: optional pre-fetched bands (avoids re-fetching when the
            caller already has them); defaults to get_scoring_bands().
    """
    bands = bands if bands is not None else get_scoring_bands()
    ordered = sorted(bands, key=lambda b: b.get("max_score", 0))
    for band in ordered:
        if score <= band.get("max_score", 0):
            return band["level"]
    return ordered[-1]["level"] if ordered else "low"


def get_baseline_risk_score(use_case_text: str) -> Dict[str, Any]:
    """Deterministic, reproducible pre-scoring pass over free text using the
    risk rules' trigger_keywords/weight fields.

    This is NOT a substitute for the Risk Assessment Agent's judgment - many
    rules (and most real risk) can't be reduced to keyword matching - but it
    gives a reproducible anchor score computed identically every time for the
    same input, independent of model variance, that the agent (and a human
    later auditing its output) can sanity-check the final assessment against.

    Args:
        use_case_text: free text to scan (e.g. the use case's name,
            description, and documentation concatenated together).
    """
    lower = (use_case_text or "").lower()
    matched: List[Dict[str, Any]] = []
    total_weight = 0

    for rule in get_risk_rules():
        keywords = rule.get("trigger_keywords") or []
        weight = rule.get("weight") or 0
        if not keywords or not weight:
            # Rules with no keywords (e.g. RISK-008's free-text condition)
            # can't be scored deterministically - they need the agent's own
            # contextual judgment, so they're intentionally left out here.
            continue
        hits = [kw for kw in keywords if kw.lower() in lower]
        if hits:
            matched.append(
                {
                    "id": rule.get("id"),
                    "title": rule.get("title") or rule.get("description"),
                    "category": rule.get("category"),
                    "weight": weight,
                    "matched_keywords": hits,
                }
            )
            total_weight += weight

    suggested_score = min(total_weight, 100)
    return {
        "matched_rules": matched,
        "suggested_score": suggested_score,
        "suggested_level": score_to_level(suggested_score),
        "method": (
            "deterministic keyword/weight match over the risk_rules table; "
            "rules without trigger_keywords are excluded and need "
            "contextual judgment instead"
        ),
    }


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


GET_BASELINE_RISK_SCORE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_baseline_risk_score",
        "description": (
            "Compute a deterministic, reproducible baseline risk score and "
            "level for a block of free text by matching the organization's "
            "risk_rules keywords and weights. Use this as a starting anchor, "
            "not a final answer - raise the score above it using judgment "
            "for factors the keyword rules miss."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "use_case_text": {
                    "type": "string",
                    "description": (
                        "Free text to scan, e.g. the use case's name, "
                        "description, and documentation concatenated."
                    ),
                },
            },
            "required": ["use_case_text"],
        },
    },
}
