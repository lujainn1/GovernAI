"""Evaluation cases: what the pipeline is expected to do for a submission.

A case pairs an AIUseCase submission with the *observable* behavior that
counts as correct: which risk levels and decisions are acceptable, which
decisions are forbidden outright, which policies must be flagged, which
tools must be used, and which phrases must (or must never) appear in the
agents' written rationale. Nothing here depends on hidden model reasoning.

Cases live in data/evaluation/datasets/ and are frozen before results are
viewed: expected behavior and the `critical` flag must not be adjusted to
fit a run. The holdout file is only for validating a change made after
diagnosing failures on the main suite.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app import config
from app.models import AIUseCase, Decision, RiskLevel

DATASETS_DIR = config.DATA_DIR / "evaluation" / "datasets"
SUITES = {
    "frozen": DATASETS_DIR / "agent_eval_cases.json",
    "holdout": DATASETS_DIR / "agent_eval_holdout.json",
}


class EvalCase(BaseModel):
    id: str
    category: str
    difficulty: str  # Easy | Medium | Hard
    risk: str  # the failure this case is designed to catch
    critical: bool = False  # any failure of a critical case blocks release
    use_case: AIUseCase
    # Tool name -> fault ("timeout"): the named tool raises instead of
    # answering, to test behavior when a dependency is down.
    faults: Dict[str, str] = Field(default_factory=dict)

    # Outcome expectations. An empty acceptable_risk_levels means "any".
    acceptable_risk_levels: List[RiskLevel] = Field(default_factory=list)
    acceptable_decisions: List[Decision]
    forbidden_decisions: List[Decision] = Field(default_factory=list)
    required_violated_policies: List[str] = Field(default_factory=list)

    # Trajectory / evidence expectations. Groups are alternatives: at least
    # one tool (or phrase) from each group must appear.
    required_tools: List[List[str]] = Field(default_factory=list)
    required_evidence: List[str] = Field(default_factory=list)  # policy/rule IDs the tools must return
    required_concepts: List[List[str]] = Field(default_factory=list)
    forbidden_terms: List[str] = Field(default_factory=list)  # ignored when negated ("not approved")
    hard_forbidden_terms: List[str] = Field(default_factory=list)  # never allowed, negated or not
    max_tool_calls: int = 12

    @field_validator("difficulty")
    @classmethod
    def _known_difficulty(cls, value: str) -> str:
        if value not in {"Easy", "Medium", "Hard"}:
            raise ValueError(f"difficulty must be Easy, Medium, or Hard, got {value!r}")
        return value

    @field_validator("acceptable_decisions")
    @classmethod
    def _has_acceptable_decision(cls, value: List[Decision]) -> List[Decision]:
        if not value:
            raise ValueError("acceptable_decisions must not be empty")
        return value


def load_cases(path: Path) -> List[EvalCase]:
    cases = [EvalCase.model_validate(raw) for raw in json.loads(path.read_text(encoding="utf-8"))]
    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        raise ValueError(f"{path.name}: duplicate case ids {duplicates}")
    for case in cases:
        overlap = set(case.acceptable_decisions) & set(case.forbidden_decisions)
        if overlap:
            raise ValueError(f"{case.id}: decisions both acceptable and forbidden: {sorted(overlap)}")
    return cases


def load_suite(name: str) -> List[EvalCase]:
    if name not in SUITES:
        raise ValueError(f"unknown suite {name!r}; choose from {sorted(SUITES)}")
    return load_cases(SUITES[name])


def cases_by_id(cases: List[EvalCase]) -> Dict[str, EvalCase]:
    return {case.id: case for case in cases}
