"""
audit_logger.py
----------------
A single, append-only audit trail for every policy decision made by
guardrails.py and every human approval decision made by approval_manager.py.

This is what lets you answer, after the fact, exactly what was requested,
what the policy decided, whether it actually executed, how long it took,
and what the result was - without ever having to trust the agent's own
words about what happened. Entries are also saved to data/audit_log.json,
so the log survives a restart of the app.
"""

import json
import os
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
AUDIT_LOG_FILE = os.path.join(DATA_DIR, "audit_log.json")


def _load_from_disk() -> List[Dict[str, Any]]:
    if os.path.exists(AUDIT_LOG_FILE):
        try:
            with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


# The in-memory audit trail, pre-loaded with whatever was saved last time.
_AUDIT_LOG: List[Dict[str, Any]] = _load_from_disk()


def _save_to_disk() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(AUDIT_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(_AUDIT_LOG, f, indent=2, default=str)


def log_event(
    timestamp: str,
    agent: str,
    machine_id: Optional[str],
    requested_tool: str,
    arguments: Dict[str, Any],
    policy_decision: str,
    reason: str,
    executed: bool,
    reviewer: Optional[str] = None,
    result: Any = None,
    latency_ms: float = 0.0,
) -> None:
    """Append one audit event and persist the log. Entries are never edited or removed."""
    _AUDIT_LOG.append(
        {
            "timestamp": timestamp,
            "agent": agent,
            "machine_id": machine_id,
            "requested_tool": requested_tool,
            "arguments": arguments,
            "policy_decision": policy_decision,
            "reason": reason,
            "executed": executed,
            "reviewer": reviewer,
            "result": result,
            "latency_ms": latency_ms,
        }
    )
    _save_to_disk()


def get_audit_log() -> List[Dict[str, Any]]:
    """Return a copy of the full audit log, oldest first."""
    return list(_AUDIT_LOG)


def reset() -> None:
    """Clear the audit log (in memory and on disk). Used by tests so each test starts clean."""
    _AUDIT_LOG.clear()
    _save_to_disk()
