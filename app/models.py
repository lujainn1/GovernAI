"""Pydantic data models shared across the governance platform."""
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ComplianceStatus(str, Enum):
    COMPLIANT = "compliant"
    PARTIALLY_COMPLIANT = "partially_compliant"
    NON_COMPLIANT = "non_compliant"


class Decision(str, Enum):
    APPROVE = "approve"
    REQUIRE_HUMAN_APPROVAL = "require_human_approval"
    BLOCK = "block"


class ReviewVerdict(str, Enum):
    APPROVED = "approved"
    NEEDS_REVISION = "needs_revision"


class ReportStatus(str, Enum):
    COMPLETED = "completed"
    PENDING_HUMAN_APPROVAL = "pending_human_approval"
    BLOCKED = "blocked"
    APPROVED_BY_HUMAN = "approved_by_human"
    REJECTED_BY_HUMAN = "rejected_by_human"


class AIUseCase(BaseModel):
    """The intake form describing an AI system / agent to be governed."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    owner: str
    data_classification: Optional[str] = None  # public | internal | confidential | restricted
    deployment_context: Optional[str] = None  # e.g. internal-tool, customer-facing, autonomous-agent
    autonomy_level: Optional[str] = None  # human-in-the-loop | human-on-the-loop | fully-autonomous
    documentation: Optional[str] = None  # free-text excerpt of design docs / DPIA / etc.


class SuggestedRiskRule(BaseModel):
    """A candidate risk-scoring rule proposed by the Risk Assessment Agent for
    human governance review. Never auto-applied to the risk_rules table and
    never allowed to influence the score of the assessment that produced it."""

    title: str
    category: str
    condition: str  # plain-language description of what should trigger the rule
    suggested_weight: Optional[int] = Field(default=None, ge=1, le=25)  # None = qualitative
    rationale: str  # why no existing rule covers this


class RiskAssessmentResult(BaseModel):
    risk_level: RiskLevel
    risk_score: int = Field(ge=0, le=100)
    risk_factors: List[str] = Field(default_factory=list)
    rationale: str
    suggested_new_rules: List[SuggestedRiskRule] = Field(default_factory=list)


class PolicyComplianceResult(BaseModel):
    status: ComplianceStatus
    violated_policies: List[str] = Field(default_factory=list)
    satisfied_policies: List[str] = Field(default_factory=list)
    rationale: str


class DecisionResult(BaseModel):
    decision: Decision
    conditions: List[str] = Field(default_factory=list)
    rationale: str


class ReviewResult(BaseModel):
    """The Review Agent's critique of the Risk / Policy / Decision output.

    `issues` are substantive problems (only meaningful with a
    `needs_revision` verdict); `suggestions` are concrete fixes and can
    also accompany an `approved` verdict as non-blocking improvements."""

    verdict: ReviewVerdict
    issues: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    rationale: str


class HumanApproval(BaseModel):
    approved: bool
    approver: str
    notes: Optional[str] = None
    decided_at: str = Field(default_factory=utcnow_iso)


class GovernanceReport(BaseModel):
    use_case: AIUseCase
    risk_assessment: RiskAssessmentResult
    policy_compliance: PolicyComplianceResult
    decision: DecisionResult
    status: ReportStatus
    created_at: str = Field(default_factory=utcnow_iso)
    updated_at: str = Field(default_factory=utcnow_iso)
    human_approval: Optional[HumanApproval] = None


class PipelineAgent(str, Enum):
    """The agents that produce a step a human approves or rejects. `decision`
    and `review` can repeat (once per revision round)."""

    RISK_ASSESSMENT = "risk_assessment"
    POLICY_COMPLIANCE = "policy_compliance"
    DECISION = "decision"
    REVIEW = "review"


class StepStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PipelineRunStatus(str, Enum):
    AWAITING_STEP_APPROVAL = "awaiting_step_approval"
    COMPLETED = "completed"  # every step approved; the GovernanceReport exists
    REJECTED = "rejected"  # a human rejected a step; the run stopped there


class PipelineStep(BaseModel):
    """One agent's output in a step-by-step run, plus the human verdict on
    it. `seq` is 1-based and identifies the step to approve or reject, so a
    stale click can never approve a step the person hasn't seen."""

    seq: int
    agent: PipelineAgent
    revision: int = 0  # 0 = first pass; n = the nth revision round (decision/review only)
    output: Dict[str, Any]  # the agent's result model, as JSON
    status: StepStatus = StepStatus.PENDING
    decided_by: Optional[str] = None
    notes: Optional[str] = None
    decided_at: Optional[str] = None
    created_at: str = Field(default_factory=utcnow_iso)


class PipelineRun(BaseModel):
    """A governance run paused for human approval after every agent step.
    `report` is only set once the run is completed."""

    use_case: AIUseCase
    status: PipelineRunStatus
    steps: List[PipelineStep] = Field(default_factory=list)
    report: Optional[GovernanceReport] = None
    # Precedent recalled from long-term memory when the run started. Internal:
    # persisted so later agents see what the first one did, never sent to clients.
    memory_context: str = Field(default="", exclude=True)
    created_at: str = Field(default_factory=utcnow_iso)
    updated_at: str = Field(default_factory=utcnow_iso)


class DocumentLanguage(str, Enum):
    ARABIC = "arabic"
    ENGLISH = "english"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class DocumentProcessingResult(BaseModel):
    """Structured output of the Document Processing Agent for one uploaded
    file. `extracted_text` is what gets folded into an AIUseCase's
    `documentation` field so the Risk and Policy agents can see it."""

    file_name: str
    file_type: str  # file extension, e.g. "pdf" or "docx"
    detected_language: DocumentLanguage
    extracted_text: str
    page_count: Optional[int] = None
    document_type: str = "unknown"
    word_count: int = 0
    ocr_used: bool = False
    warnings: List[str] = Field(default_factory=list)


class DocumentGovernanceReport(BaseModel):
    """Response shape for the document-upload governance flow: the raw
    Document Processing Agent output alongside the GovernanceReport that
    resulted from feeding its extracted text through the existing
    Risk/Policy/Decision pipeline."""

    document: DocumentProcessingResult
    report: GovernanceReport


class AuditEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    use_case_id: str
    stage: str
    actor: str
    timestamp: str = Field(default_factory=utcnow_iso)
    data: Dict[str, Any] = Field(default_factory=dict)
