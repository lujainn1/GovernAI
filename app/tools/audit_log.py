"""Tool: append-only audit log.

Every stage of the governance workflow (intake, risk assessment, policy
check, decision, human approval) writes an AuditEntry here. Backed by the
Supabase `audit_log` table, which foreign-keys to `use_cases` so entries
always trace back to a real use case.
"""
from typing import Any, Dict, List, Optional

from app import db
from app.models import AuditEntry

TABLE = "audit_log"


def log_event(use_case_id: str, stage: str, actor: str, data: Optional[Dict[str, Any]] = None) -> AuditEntry:
    """Append an audit entry.

    Args:
        use_case_id: id of the AI use case this event relates to.
        stage: workflow stage, e.g. intake, risk_assessment, policy_compliance,
            decision, human_approval.
        actor: who/what produced this event (agent name, "system", or a
            human's identifier/email).
        data: arbitrary JSON-serializable payload for this event.
    """
    entry = AuditEntry(use_case_id=use_case_id, stage=stage, actor=actor, data=data or {})
    db.insert(
        TABLE,
        {
            "id": entry.id,
            "use_case_id": entry.use_case_id,
            "stage": entry.stage,
            "actor": entry.actor,
            "data": entry.data,
            "created_at": entry.timestamp,
        },
    )
    return entry


def get_audit_log(use_case_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read audit entries, optionally filtered to a single use case."""
    params: Dict[str, Any] = {"order": "created_at.asc"}
    if use_case_id is not None:
        params["use_case_id"] = f"eq.{use_case_id}"

    return [
        {
            "id": row["id"],
            "use_case_id": row["use_case_id"],
            "stage": row["stage"],
            "actor": row["actor"],
            "timestamp": row["created_at"],
            "data": row.get("data") or {},
        }
        for row in db.select(TABLE, params)
    ]


LOG_EVENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "log_event",
        "description": (
            "Append an entry to the compliance audit log for this use case. "
            "Use this to record notable findings or reasoning checkpoints, "
            "in addition to the automatic per-stage logging the platform does."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "use_case_id": {"type": "string", "description": "The AI use case id."},
                "stage": {"type": "string", "description": "Workflow stage this note belongs to."},
                "actor": {"type": "string", "description": "Name of the agent logging this event."},
                "data": {"type": "object", "description": "Arbitrary JSON payload to record."},
            },
            "required": ["use_case_id", "stage", "actor"],
        },
    },
}
