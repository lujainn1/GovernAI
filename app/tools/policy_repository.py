"""Tool: read-only access to the SDAIA-derived control library.

Backed by data/policies.yaml. Exposed to the Policy Compliance and
Decision Agents as OpenAI-style callable tools for lookup/citation. Which
controls actually apply to a given use case is decided deterministically
by app/tools/control_router.py, not by the LLM.
"""
from functools import lru_cache
from typing import Any, Dict, List, Optional

import yaml

from app import config


@lru_cache(maxsize=1)
def _load_policies() -> List[Dict[str, Any]]:
    with open(config.POLICIES_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("controls", [])


def get_policies(module: Optional[str] = None, critical_only: bool = False) -> List[Dict[str, Any]]:
    """Return controls from the library, optionally filtered.

    Args:
        module: AI_ETHICS | PDPL | XFER | GEN
        critical_only: only return controls flagged critical.
    """
    policies = _load_policies()
    if module:
        policies = [p for p in policies if p.get("module") == module]
    if critical_only:
        policies = [p for p in policies if p.get("critical")]
    return policies


def search_policies(query: str) -> List[Dict[str, Any]]:
    """Keyword search over control text, principle, and control id."""
    q = query.lower()
    return [
        p
        for p in _load_policies()
        if q in p.get("control", "").lower()
        or q in p.get("principle", "").lower()
        or q in p.get("id", "").lower()
    ]


GET_POLICIES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_policies",
        "description": (
            "Retrieve controls from the SDAIA-derived control library, optionally "
            "filtered by module (AI_ETHICS, PDPL, XFER, GEN) or critical-only. "
            "Use this for background/citation - which controls apply to the current "
            "use case has already been decided by the deterministic router."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "enum": ["AI_ETHICS", "PDPL", "XFER", "GEN"],
                    "description": "Optional module filter.",
                },
                "critical_only": {
                    "type": "boolean",
                    "description": "Only return controls flagged critical.",
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
        "description": "Keyword search the control library by control text, principle, or control id.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword or phrase to search for."},
            },
            "required": ["query"],
        },
    },
}
