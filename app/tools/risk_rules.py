"""Tool: read-only access to the SDAIA-derived risk library.

Backed by data/risk_rules.yaml. Exposed to the Risk Assessment Agent as an
OpenAI-style callable tool for background/citation. Which risks actually
apply to a given use case, and their score, is decided deterministically
by app/tools/risk_engine.py - not by the LLM.
"""
from functools import lru_cache
from typing import Any, Dict, List, Optional

import yaml

from app import config


@lru_cache(maxsize=1)
def _load_risk_rules() -> Dict[str, Any]:
    with open(config.RISK_RULES_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_risk_rules(domain: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return risk library entries, optionally filtered by domain.

    Args:
        domain: optional domain filter, e.g. Data, Algorithm, Human,
            Security, "Third Party", Compliance, Legal, Operational,
            Reputational, "Social/Environmental", Accountability, GenAI.
    """
    rules = _load_risk_rules().get("risk_library", [])
    if domain:
        rules = [r for r in rules if r.get("domain", "").lower() == domain.lower()]
    return rules


def get_scoring_bands() -> List[Dict[str, Any]]:
    """Return the score->risk-level bands used to translate a numeric score."""
    return _load_risk_rules().get("scoring", {}).get("bands", [])


GET_RISK_RULES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_risk_rules",
        "description": (
            "Retrieve entries from the SDAIA-derived risk library for background/citation, "
            "optionally filtered by domain. The overall risk level and score for this "
            "submission are already computed deterministically - this tool does not change them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": (
                        "Optional domain filter: Data, Algorithm, Human, Security, "
                        "Third Party, Compliance, Legal, Operational, Reputational, "
                        "Social/Environmental, Accountability, or GenAI."
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
        "description": "Retrieve the numeric score ranges that map to low/medium/high/critical risk levels.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
