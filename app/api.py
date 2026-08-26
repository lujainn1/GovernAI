"""FastAPI application exposing the governance workflow over HTTP."""
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.llm_client import ConfigurationError
from app.models import AIUseCase, AuditEntry, GovernanceReport
from app.orchestrator import (
    GovernanceOrchestrator,
    InvalidApprovalStateError,
    UseCaseNotFoundError,
)
from app.reports import list_reports, load_report
from app.tools.audit_log import get_audit_log
from app.tools.policy_repository import get_policies
from app.tools.risk_rules import get_risk_rules

app = FastAPI(
    title="GovernAI",
    description="Multi-Agent AI Governance Platform",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class UseCaseSubmission(BaseModel):
    name: str
    description: str
    owner: str
    data_classification: Optional[str] = None
    deployment_context: Optional[str] = None
    autonomy_level: Optional[str] = None
    documentation: Optional[str] = None
    personal_data: bool = False
    sensitive_data: bool = False
    generative_ai: bool = False
    external_provider: bool = False
    data_outside_ksa: bool = False
    high_impact_decision: bool = False
    user_facing_chat: bool = False
    research_purpose: bool = False
    deployment_status: str = "development"


class ApprovalRequest(BaseModel):
    approved: bool
    approver: str
    notes: Optional[str] = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/policies")
def list_policies() -> list:
    return get_policies()


@app.get("/risk-rules")
def list_risk_rules() -> list:
    return get_risk_rules()


@app.post("/use-cases", response_model=GovernanceReport)
def submit_use_case(payload: UseCaseSubmission) -> GovernanceReport:
    use_case = AIUseCase(**payload.model_dump())
    orchestrator = GovernanceOrchestrator()
    try:
        return orchestrator.run(use_case)
    except ConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/use-cases", response_model=List[GovernanceReport])
def get_all_reports() -> List[GovernanceReport]:
    return list_reports()


@app.get("/use-cases/{use_case_id}", response_model=GovernanceReport)
def get_report(use_case_id: str) -> GovernanceReport:
    report = load_report(use_case_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Use case not found")
    return report


@app.post("/use-cases/{use_case_id}/approve", response_model=GovernanceReport)
def approve_use_case(use_case_id: str, payload: ApprovalRequest) -> GovernanceReport:
    orchestrator = GovernanceOrchestrator()
    try:
        return orchestrator.apply_human_decision(
            use_case_id, approved=payload.approved, approver=payload.approver, notes=payload.notes
        )
    except UseCaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Use case not found") from exc
    except InvalidApprovalStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/audit-log", response_model=List[AuditEntry])
def audit_log(use_case_id: Optional[str] = None) -> List[AuditEntry]:
    return get_audit_log(use_case_id)
