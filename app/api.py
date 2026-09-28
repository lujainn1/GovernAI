"""FastAPI application exposing the governance workflow over HTTP."""

import logging
import time
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.agents.document_agent import DocumentProcessingAgent, DocumentProcessingError
from app.auth import get_current_user
from app.config import CORS_ORIGINS
from app.llm_client import ConfigurationError
from app.models import (
    AIUseCase,
    AuditEntry,
    DocumentGovernanceReport,
    DocumentProcessingResult,
    GovernanceReport,
)
from app.orchestrator import (
    GovernanceOrchestrator,
    InvalidApprovalStateError,
    UseCaseNotFoundError,
)
from app.observability.context import request_context
from app.observability.health import health_for_window
from app.observability.logging_config import configure_logging, emit
from app.observability.metrics import metrics_for_window
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


http_logger = logging.getLogger("governai.http")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    yield


app = FastAPI(
    title="GovernAI",
    description="Multi-Agent AI Governance Platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Give every request an id (or adopt a well-formed X-Request-ID from the
    caller), attach it to all log lines and agent runs it causes, echo it in
    the response, and log one line per request."""
    started = time.perf_counter()
    with request_context(request.headers.get("x-request-id")) as request_id:
        try:
            response = await call_next(request)
        except Exception:
            emit(
                http_logger, "http_request", logging.ERROR,
                method=request.method, path=request.url.path, status=500,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            raise
        response.headers["X-Request-ID"] = request_id
        emit(
            http_logger, "http_request",
            method=request.method, path=request.url.path, status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return response


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
)
def submit_use_case(
    payload: UseCaseSubmission,
    user: dict = Depends(get_current_user),
) -> GovernanceReport:
    use_case = AIUseCase(**payload.model_dump())

    orchestrator = GovernanceOrchestrator()

    try:
        return orchestrator.run(use_case, created_by=user.get("id"))
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc


@app.post(
    "/use-cases/with-document",
    response_model=DocumentGovernanceReport,
)
async def submit_use_case_with_document(
    name: str = Form(...),
    description: str = Form(...),
    owner: str = Form(...),
    data_classification: Optional[str] = Form(None),
    deployment_context: Optional[str] = Form(None),
    autonomy_level: Optional[str] = Form(None),
    documentation: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
) -> DocumentGovernanceReport:
    """Same governance pipeline as POST /use-cases, but the documentation
    comes from an uploaded PDF/DOCX instead of (or in addition to) pasted
    text: the file goes through the Document Processing Agent first, and
    its extracted text feeds the existing Risk/Policy/Decision agents via
    GovernanceOrchestrator.run_with_document."""
    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"{file.filename} exceeds the 25 MB upload limit.",
        )

    use_case = AIUseCase(
        name=name,
        description=description,
        owner=owner,
        data_classification=data_classification,
        deployment_context=deployment_context,
        autonomy_level=autonomy_level,
        documentation=documentation,
    )

    orchestrator = GovernanceOrchestrator()

    try:
        report, doc_result = orchestrator.run_with_document(
            use_case, file.filename, content, created_by=user.get("id")
        )
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except DocumentExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DocumentProcessingError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return DocumentGovernanceReport(document=doc_result, report=report)


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
# OBSERVABILITY
# =========================================================

@app.get("/metrics", dependencies=[Depends(get_current_user)])
def get_metrics(hours: int = Query(24, ge=1, le=720)) -> dict:
    """Success rate, avg/P95 latency, per-tool usage and error rate, token
    cost, and decision mix over the last `hours` hours of agent runs."""
    return metrics_for_window(hours)


@app.get("/metrics/health-report", dependencies=[Depends(get_current_user)])
def get_health_report(hours: int = Query(24, ge=1, le=720)) -> dict:
    """Overall HEALTHY/DEGRADED/CRITICAL status, classified failures, latency
    anomalies (mean + 2 std), and recommended actions."""
    return health_for_window(hours)


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


@app.post(
    "/documents/process",
    response_model=DocumentProcessingResult,
    dependencies=[Depends(get_current_user)],
)
async def process_document(file: UploadFile = File(...)) -> DocumentProcessingResult:
    """Document Processing Agent endpoint: PDF/DOCX in, structured output
    (language, extracted text, page count, document type) out. Separate
    from /documents/extract (used by the existing Submit form) so that
    endpoint's behavior is unaffected."""
    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"{file.filename} exceeds the 25 MB upload limit.",
        )

    try:
        return DocumentProcessingAgent().process(file.filename, content)
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except DocumentExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DocumentProcessingError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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