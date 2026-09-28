"""Persistence for step-by-step (human-in-the-loop) pipeline runs, backed by
the Supabase `pipeline_runs` table (one row per use case).

A run is paused between requests - after each agent, until a person approves
or rejects its output - so everything needed to resume it lives in that row:
the ordered steps (each agent's output and the human verdict on it) and the
memory context the run started with.
"""
from typing import List, Optional

from app import db
from app.models import (
    AIUseCase,
    PipelineRun,
    PipelineRunStatus,
    PipelineStep,
    utcnow_iso,
)
from app.reports import USE_CASES_TABLE, load_report

TABLE = "pipeline_runs"


def _row(run: PipelineRun) -> dict:
    return {
        "use_case_id": run.use_case.id,
        "status": run.status.value,
        "memory_context": run.memory_context,
        "steps": [step.model_dump(mode="json") for step in run.steps],
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _run_from_rows(use_case_row: dict, run_row: dict) -> PipelineRun:
    return PipelineRun(
        use_case=AIUseCase(**use_case_row),
        status=PipelineRunStatus(run_row["status"]),
        steps=[PipelineStep(**step) for step in run_row.get("steps") or []],
        memory_context=run_row.get("memory_context") or "",
        created_at=run_row["created_at"],
        updated_at=run_row["updated_at"],
    )


def save_run(run: PipelineRun) -> None:
    run.updated_at = utcnow_iso()
    db.upsert(TABLE, _row(run), on_conflict="use_case_id")


def load_run(use_case_id: str) -> Optional[PipelineRun]:
    """The run for a use case, with its GovernanceReport attached once the
    run is completed. None if the use case has no step-by-step run."""
    run_rows = db.select(TABLE, {"use_case_id": f"eq.{use_case_id}"})
    if not run_rows:
        return None
    use_case_rows = db.select(USE_CASES_TABLE, {"id": f"eq.{use_case_id}"})
    if not use_case_rows:
        return None

    run = _run_from_rows(use_case_rows[0], run_rows[0])
    if run.status == PipelineRunStatus.COMPLETED:
        run.report = load_report(use_case_id)
    return run


def list_runs(status: Optional[PipelineRunStatus] = None) -> List[PipelineRun]:
    """Runs, newest first, optionally only those with `status`. The reports
    of completed runs are not attached (they are in the reports list)."""
    params = {"order": "created_at.desc"}
    if status is not None:
        params["status"] = f"eq.{status.value}"
    run_rows = db.select(TABLE, params)
    if not run_rows:
        return []

    use_cases_by_id = {row["id"]: row for row in db.select(USE_CASES_TABLE)}
    return [
        _run_from_rows(use_cases_by_id[row["use_case_id"]], row)
        for row in run_rows
        if row["use_case_id"] in use_cases_by_id
    ]
