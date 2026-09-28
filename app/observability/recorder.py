"""Persist an agent's execution trace as an `agent_runs` row.

Recording is best-effort: observability must never take the governance
pipeline down, so a failed insert is logged and swallowed.
"""
import json
import logging
import uuid
from typing import Any, Dict, Optional

from app import db
from app.evaluation.checks import tool_succeeded
from app.observability.context import get_request_id
from app.observability.logging_config import emit
from app.observability.pricing import estimate_cost_usd

TABLE = "agent_runs"
_PREVIEW_CHARS = 200

logger = logging.getLogger("governai.observability")


def _preview(value: Any) -> str:
    return json.dumps(value, default=str)[:_PREVIEW_CHARS]


def _compact_tool_call(call: Dict[str, Any]) -> Dict[str, Any]:
    """Enough to see what ran and how it went, without persisting full
    arguments or outputs (which can contain submitted documentation)."""
    return {
        "tool": call["tool"],
        "ok": tool_succeeded(call["result"]),
        "latency_ms": call["latency_ms"],
        "arguments_preview": _preview(call["arguments"]),
        "output_preview": _preview(call["result"]),
    }


def build_run_row(
    trace: Dict[str, Any],
    *,
    agent: str,
    stage: str,
    use_case_id: str,
    audit_log_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    error = trace.get("error")
    tool_calls = [_compact_tool_call(call) for call in trace["tool_calls"]]
    return {
        "id": str(uuid.uuid4()),
        "use_case_id": use_case_id,
        "audit_log_id": audit_log_id,
        "request_id": request_id if request_id is not None else get_request_id(),
        "agent": agent,
        "stage": stage,
        "model": trace["model"],
        "status": "error" if error else "success",
        "error_type": error["type"] if error else None,
        "error_message": error["message"] if error else None,
        "started_at": trace["started_at"],
        "latency_ms": trace["latency_ms"],
        "iterations": trace["iterations"],
        "tool_call_count": len(tool_calls),
        "tool_error_count": sum(1 for call in tool_calls if not call["ok"]),
        "prompt_tokens": trace["prompt_tokens"],
        "completion_tokens": trace["completion_tokens"],
        "total_tokens": trace["total_tokens"],
        "estimated_cost_usd": estimate_cost_usd(
            trace["model"], trace["prompt_tokens"], trace["completion_tokens"], trace["total_tokens"]
        ),
        "steps": trace["steps"],
        "tool_calls": tool_calls,
    }


def record_agent_run(
    agent: Any,
    *,
    stage: str,
    use_case_id: str,
    audit_log_id: Optional[str] = None,
) -> Optional[str]:
    """Save `agent.last_trace`. Returns the new run id, or None when the
    agent never ran (nothing to record) or the insert failed."""
    trace = agent.last_trace
    if not trace.get("started_at"):
        return None

    row = build_run_row(
        trace, agent=agent.name, stage=stage, use_case_id=use_case_id, audit_log_id=audit_log_id
    )
    try:
        db.insert(TABLE, row)
    except Exception as exc:  # observability must not break governance
        emit(
            logger,
            "agent_run_persist_failed",
            logging.WARNING,
            agent=agent.name,
            use_case_id=use_case_id,
            error_type=type(exc).__name__,
        )
        return None
    return row["id"]
