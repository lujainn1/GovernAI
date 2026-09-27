"""
observability.py
------------------
Structured, observable logging for every agent run.

Nothing here logs private model reasoning - only things you could observe
from outside the agent: which step happened, which tool was proposed,
what the guardrail decided, what the tool returned, how long it took, and
what the final response was.

Every run gets its own ExecutionTrace with a unique trace_id, so a single
run can be inspected end to end:

    User Query -> Agent Decision -> Tool Call -> Guardrail Decision
               -> Tool Result -> Human Approval (if required) -> Final Result

Traces are kept in memory for the life of the app (so the dashboard can
list and inspect them) and are also saved to data/traces.json, so they
survive a restart and can be exported as JSON.
"""

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TRACES_FILE = os.path.join(DATA_DIR, "traces.json")

# The only kinds of events this project ever logs. Keeping this list
# explicit makes it easy to see, at a glance, everything that is (and is
# not) recorded.
EVENT_TYPES = [
    "agent_start",
    "decision",
    "tool_call",
    "tool_result",
    "guardrail_decision",
    "approval_request",
    "approval_result",
    "final_result",
    "error",
    "safe_stop",
]


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


@dataclass
class TraceEvent:
    """One structured, observable event within a run."""

    trace_id: str
    timestamp: str
    step_number: int
    event_type: str
    agent_name: str
    machine_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments: Optional[Dict[str, Any]] = None
    result: Any = None
    status: str = "ok"
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ExecutionTrace:
    """
    One agent run, start to finish. Every proposed action, its guardrail
    decision, its result (if any), and the final response are recorded
    here, in order, so the run can be replayed and inspected later.
    """

    def __init__(self, agent_name: str, query: str):
        self.trace_id = uuid.uuid4().hex[:10]
        self.agent_name = agent_name
        self.query = query
        self._start_time = time.perf_counter()
        self.started_at = _now()
        self.events: List[TraceEvent] = []
        self.status = "running"
        self.total_latency_ms: float = 0.0
        _register(self)
        self.log_event("agent_start", metadata={"query": query})

    # --- logging -----------------------------------------------------

    def log_event(
        self,
        event_type: str,
        machine_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        tool_arguments: Optional[Dict[str, Any]] = None,
        result: Any = None,
        status: str = "ok",
        latency_ms: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TraceEvent:
        """Append one event to this trace. step_number is assigned automatically."""
        event = TraceEvent(
            trace_id=self.trace_id,
            timestamp=_now(),
            step_number=len(self.events) + 1,
            event_type=event_type,
            agent_name=self.agent_name,
            machine_id=machine_id,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            result=result,
            status=status,
            latency_ms=latency_ms,
            metadata=metadata or {},
        )
        self.events.append(event)
        return event

    def finish(self, status: str, final_result: Any) -> None:
        """Close out this run: record its status, total latency, and final answer."""
        self.total_latency_ms = round((time.perf_counter() - self._start_time) * 1000, 2)
        self.status = status
        self.log_event("final_result", status=status, result=final_result, latency_ms=self.total_latency_ms)
        save_all()

    # --- loop detection helper (used by guardrails.py) ----------------

    def tool_call_count(self) -> int:
        """How many tool_call events have already been logged for this run."""
        return sum(1 for event in self.events if event.event_type == "tool_call")

    def has_identical_prior_call(self, tool_name: str, arguments: Dict[str, Any]) -> bool:
        """
        True if this exact tool + arguments combination was already proposed
        earlier in this same run - the signature of an agent stuck in a loop.
        """
        signature = tuple(sorted(arguments.items()))
        for event in self.events:
            if event.event_type != "tool_call" or event.tool_name != tool_name:
                continue
            if event.tool_arguments and tuple(sorted(event.tool_arguments.items())) == signature:
                return True
        return False

    # --- export --------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "agent_name": self.agent_name,
            "query": self.query,
            "started_at": self.started_at,
            "status": self.status,
            "total_latency_ms": self.total_latency_ms,
            "steps": [event.to_dict() for event in self.events],
        }

    def to_json(self) -> str:
        """Export this trace exactly as described in the project's JSON export example."""
        return json.dumps(self.to_dict(), indent=2, default=str)


# --- module-level trace registry -------------------------------------

_TRACES: Dict[str, ExecutionTrace] = {}


def _register(trace: ExecutionTrace) -> None:
    _TRACES[trace.trace_id] = trace


def get_trace(trace_id: str) -> Optional[ExecutionTrace]:
    """Look up one trace by its trace_id (used by approval_manager.py and the dashboard)."""
    return _TRACES.get(trace_id)


def list_traces() -> List[ExecutionTrace]:
    """Every trace created since the app started, oldest first."""
    return list(_TRACES.values())


def save_all() -> None:
    """Write every known trace to data/traces.json, overwriting the previous contents."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TRACES_FILE, "w", encoding="utf-8") as f:
        json.dump([trace.to_dict() for trace in _TRACES.values()], f, indent=2, default=str)


def reset() -> None:
    """Clear all in-memory traces. Used by tests so each test starts clean."""
    _TRACES.clear()
