"""FastAPI application exposing the governance workflow over HTTP."""

from typing import List, Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.auth import get_current_user
from app.llm_client import ConfigurationError
from app.models import AIUseCase, AuditEntry, GovernanceReport
from app.orchestrator import (
    GovernanceOrchestrator,
    InvalidApprovalStateError,
    UseCaseNotFoundError,
)
from app.reports import list_reports, load_report
from app.tools.audit_log import get_audit_log
from app.tools.document_extract import (
    MAX_FILE_SIZE,
    DocumentExtractionError,
    UnsupportedFileTypeError,
    extract_text,
)
from app.tools.policy_repository import add_policy, get_policies
from app.tools.risk_rules import add_risk_rule, get_risk_rules


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


class ApprovalRequest(BaseModel):
    approved: bool
    approver: str
    notes: Optional[str] = None


class PolicySubmission(BaseModel):
    id: str
    title: str
    category: str
    description: str
    status: str = "active"
    coverage: int = 100
    min_risk_level: str = "low"


class RiskRuleSubmission(BaseModel):
    id: str
    title: str
    category: str
    severity: str
    condition: str
    action: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# =========================================================
# POLICIES
# =========================================================

@app.get("/policies", dependencies=[Depends(get_current_user)])
def list_policies() -> list:
    return get_policies()


@app.post("/policies", dependencies=[Depends(get_current_user)])
def create_policy(payload: PolicySubmission) -> dict:
    policy = payload.model_dump()

    try:
        return add_policy(policy)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


# =========================================================
# RISK RULES
# =========================================================

@app.get("/risk-rules", dependencies=[Depends(get_current_user)])
def list_risk_rules() -> list:
    return get_risk_rules()


@app.post("/risk-rules", dependencies=[Depends(get_current_user)])
def create_risk_rule(payload: RiskRuleSubmission) -> dict:
    rule = payload.model_dump()

    try:
        return add_risk_rule(rule)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


# =========================================================
# AI USE CASES
# =========================================================

@app.post(
    "/use-cases",
    response_model=GovernanceReport,
    dependencies=[Depends(get_current_user)],
)
def submit_use_case(
    payload: UseCaseSubmission,
) -> GovernanceReport:
    use_case = AIUseCase(**payload.model_dump())

    orchestrator = GovernanceOrchestrator()

    try:
        return orchestrator.run(use_case)
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc


@app.get(
    "/use-cases",
    response_model=List[GovernanceReport],
    dependencies=[Depends(get_current_user)],
)
def get_all_reports() -> List[GovernanceReport]:
    return list_reports()


@app.get(
    "/use-cases/{use_case_id}",
    response_model=GovernanceReport,
    dependencies=[Depends(get_current_user)],
)
def get_report(
    use_case_id: str,
) -> GovernanceReport:
    report = load_report(use_case_id)

    if report is None:
        raise HTTPException(
            status_code=404,
            detail="Use case not found",
        )

    return report


@app.post(
    "/use-cases/{use_case_id}/approve",
    response_model=GovernanceReport,
    dependencies=[Depends(get_current_user)],
)
def approve_use_case(
    use_case_id: str,
    payload: ApprovalRequest,
) -> GovernanceReport:
    orchestrator = GovernanceOrchestrator()

    try:
        return orchestrator.apply_human_decision(
            use_case_id,
            approved=payload.approved,
            approver=payload.approver,
            notes=payload.notes,
        )

    except UseCaseNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Use case not found",
        ) from exc

    except InvalidApprovalStateError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


# =========================================================
# DOCUMENTS
# =========================================================

@app.post("/documents/extract", dependencies=[Depends(get_current_user)])
async def extract_document(file: UploadFile = File(...)) -> dict:
    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"{file.filename} exceeds the 25 MB upload limit.",
        )

    try:
        result = extract_text(file.filename, content)
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except DocumentExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "filename": file.filename,
        "size": len(content),
        "text": result.text,
        "word_count": result.word_count,
        "pages": result.pages,
    }


# =========================================================
# AUDIT LOG
# =========================================================

@app.get(
    "/audit-log",
    response_model=List[AuditEntry],
    dependencies=[Depends(get_current_user)],
)
def audit_log(
    use_case_id: Optional[str] = None,
) -> List[AuditEntry]:
    return get_audit_log(use_case_id)