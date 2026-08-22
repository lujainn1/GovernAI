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


class RiskAssessmentResult(BaseModel):
    risk_level: RiskLevel
    risk_score: int = Field(ge=0, le=100)
    risk_factors: List[str] = Field(default_factory=list)
    rationale: str


class PolicyComplianceResult(BaseModel):
    status: ComplianceStatus
    violated_policies: List[str] = Field(default_factory=list)
    satisfied_policies: List[str] = Field(default_factory=list)
    rationale: str


class DecisionResult(BaseModel):
    decision: Decision
    conditions: List[str] = Field(default_factory=list)
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


class AuditEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    use_case_id: str
    stage: str
    actor: str
    timestamp: str = Field(default_factory=utcnow_iso)
    data: Dict[str, Any] = Field(default_factory=dict)
