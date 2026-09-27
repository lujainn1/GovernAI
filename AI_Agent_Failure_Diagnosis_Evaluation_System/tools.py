"""
tools.py
--------
Every tool the agent can propose, plus the simulated factory data they read
from or act on. Tools fall into three categories, which guardrails.py uses
to decide whether a proposal is ALLOWed, requires human approval, or is
DENIED:

    Read-only (always ALLOW):
        check_machine_status, get_maintenance_history, predict_failure_risk

    Sensitive (REQUIRES_APPROVAL):
        stop_machine, schedule_emergency_maintenance

    Forbidden (always DENY - defined here only so guardrail tests can prove
    they never actually run):
        delete_machine_data, modify_safety_logs

Nothing in this file decides policy - it only implements what each tool
does *if* it is allowed to run. That decision always happens in
guardrails.py first.
"""

from typing import Any, Dict, List, Optional

# Simulated data for the three machines used throughout this project.
MACHINES: Dict[str, Dict[str, str]] = {
    "CNC-01": {
        "status": "High vibration. Normal temperature. Running at 85% capacity.",
        "history": "Bearing replaced twice this year.",
        "risk_level": "High",
        "risk_detail": "High risk of failure within 48 hours due to abnormal vibration.",
    },
    "ROBOT-03": {
        "status": "Normal operation. No anomalies detected.",
        "history": "No major issues reported.",
        "risk_level": "Low",
        "risk_detail": "Low risk. The system is stable.",
    },
    "CONVEYOR-02": {
        "status": "Elevated motor temperature. Possible bearing issue.",
        "history": "Previous motor overheating reported.",
        "risk_level": "Medium",
        "risk_detail": "Medium risk. Motor temperature should be monitored closely.",
    },
}

MACHINE_IDS = list(MACHINES.keys())

# Every side effect any tool has ever produced. In this simulated project
# nothing here touches a real machine - but recording every side effect in
# one place lets tests and the dashboard *verify* what actually ran,
# instead of trusting a tool's return text.
SIDE_EFFECTS: List[Dict[str, Any]] = []


def is_known_machine(machine_id: str) -> bool:
    """Return True if machine_id is one of the machines we have data for."""
    return machine_id in MACHINES


# --- Read-only tools (guardrails.py: always ALLOW) -----------------------

def check_machine_status(machine_id: str) -> Optional[str]:
    """Tool: return the current operational status of a machine."""
    machine = MACHINES.get(machine_id)
    return machine["status"] if machine else None


def get_maintenance_history(machine_id: str) -> Optional[str]:
    """Tool: return the previous maintenance record of a machine."""
    machine = MACHINES.get(machine_id)
    return machine["history"] if machine else None


def predict_failure_risk(machine_id: str) -> Optional[str]:
    """Tool: return the predicted failure risk as a short explanation."""
    machine = MACHINES.get(machine_id)
    return machine["risk_detail"] if machine else None


def get_risk_level(machine_id: str) -> Optional[str]:
    """Return just the risk level word ('Low', 'Medium', or 'High')."""
    machine = MACHINES.get(machine_id)
    return machine["risk_level"] if machine else None


# --- Sensitive tools (guardrails.py: REQUIRES_APPROVAL) -------------------

def stop_machine(machine_id: str, reason: str) -> Dict[str, Any]:
    """
    Sensitive tool: stop a machine. This has a real (simulated) side
    effect, so guardrails.py must never call this directly - only
    approval_manager.approve() may, and only after a human approves it.
    """
    event = {"tool": "stop_machine", "machine_id": machine_id, "reason": reason}
    SIDE_EFFECTS.append(event)
    return {"executed": True, "message": f"{machine_id} has been stopped. Reason: {reason}"}


def schedule_emergency_maintenance(machine_id: str, reason: str) -> Dict[str, Any]:
    """Sensitive tool: schedule emergency maintenance. Same approval rule as stop_machine."""
    event = {"tool": "schedule_emergency_maintenance", "machine_id": machine_id, "reason": reason}
    SIDE_EFFECTS.append(event)
    return {"executed": True, "message": f"Emergency maintenance scheduled for {machine_id}. Reason: {reason}"}


# --- Forbidden tools (guardrails.py: always DENY) -------------------------
# These exist only so a test can attempt to call them and prove that the
# policy layer blocks them before they ever run (SIDE_EFFECTS stays empty).

def delete_machine_data(machine_id: str) -> Dict[str, Any]:
    """Forbidden tool: must never run in this project."""
    event = {"tool": "delete_machine_data", "machine_id": machine_id}
    SIDE_EFFECTS.append(event)
    return {"executed": True, "message": f"Data for {machine_id} deleted."}


def modify_safety_logs(machine_id: str, entry: str) -> Dict[str, Any]:
    """Forbidden tool: must never run in this project."""
    event = {"tool": "modify_safety_logs", "machine_id": machine_id, "entry": entry}
    SIDE_EFFECTS.append(event)
    return {"executed": True, "message": f"Safety log for {machine_id} modified."}


# A single registry every dispatcher (guardrails.py, approval_manager.py)
# uses to call a tool by name. Adding a new tool means adding it here and
# giving guardrails.py a policy for it - nothing else needs to change.
ALL_TOOL_FUNCTIONS = {
    "check_machine_status": check_machine_status,
    "get_maintenance_history": get_maintenance_history,
    "predict_failure_risk": predict_failure_risk,
    "stop_machine": stop_machine,
    "schedule_emergency_maintenance": schedule_emergency_maintenance,
    "delete_machine_data": delete_machine_data,
    "modify_safety_logs": modify_safety_logs,
}

# Kept for backward compatibility with existing code that only needs the
# three read-only tools (e.g. the Part 1/2 diagnosis workflow).
TOOL_FUNCTIONS = {
    "check_machine_status": check_machine_status,
    "get_maintenance_history": get_maintenance_history,
    "predict_failure_risk": predict_failure_risk,
}
