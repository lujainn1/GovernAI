from io import BytesIO

import httpx
import openai
from docx import Document
from fastapi.testclient import TestClient

from app.agents.base import AgentError
from app.api import app
from app.models import (
    ComplianceStatus,
    Decision,
    DecisionResult,
    PolicyComplianceResult,
    ReviewResult,
    ReviewVerdict,
    RiskAssessmentResult,
    RiskLevel,
)

client = TestClient(app)


def _docx_bytes(text: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _patch_agents(monkeypatch, decision: Decision):
    risk_result = RiskAssessmentResult(
        risk_level=RiskLevel.HIGH, risk_score=75, risk_factors=["autonomy"], rationale="r"
    )
    compliance_result = PolicyComplianceResult(
        status=ComplianceStatus.NON_COMPLIANT, violated_policies=["POL-003"], satisfied_policies=[], rationale="c"
    )
    decision_result = DecisionResult(decision=decision, conditions=["add review"], rationale="d")

    monkeypatch.setattr("app.agents.risk_agent.RiskAssessmentAgent.assess", lambda self, uc: risk_result)
    monkeypatch.setattr(
        "app.agents.policy_agent.PolicyComplianceAgent.check", lambda self, uc, risk: compliance_result
    )
    monkeypatch.setattr(
        "app.agents.decision_agent.DecisionAgent.decide",
        lambda self, uc, risk, compliance, **kwargs: decision_result,
    )
    monkeypatch.setattr(
        "app.agents.review_agent.ReviewAgent.review",
        lambda self, uc, risk, compliance, decision: ReviewResult(
            verdict=ReviewVerdict.APPROVED, rationale="consistent"
        ),
    )


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_policies_and_risk_rules():
    assert client.get("/policies").status_code == 200
    assert client.get("/risk-rules").status_code == 200


def test_submit_and_fetch_and_approve_flow(monkeypatch):
    _patch_agents(monkeypatch, decision=Decision.REQUIRE_HUMAN_APPROVAL)

    submit_resp = client.post(
        "/use-cases",
        json={"name": "Test", "description": "desc", "owner": "team"},
    )
    assert submit_resp.status_code == 200
    report = submit_resp.json()
    assert report["status"] == "pending_human_approval"
    use_case_id = report["use_case"]["id"]

    get_resp = client.get(f"/use-cases/{use_case_id}")
    assert get_resp.status_code == 200

    approve_resp = client.post(
        f"/use-cases/{use_case_id}/approve",
        json={"approved": True, "approver": "jane@example.com"},
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "approved_by_human"

    audit_resp = client.get("/audit-log", params={"use_case_id": use_case_id})
    assert audit_resp.status_code == 200
    assert len(audit_resp.json()) >= 5


def test_submit_use_case_with_document_flow(monkeypatch):
    """POST /use-cases/with-document: Upload -> Document Agent ->
    GovernanceOrchestrator -> Policy Agent + Risk Agent -> Decision Agent."""
    _patch_agents(monkeypatch, decision=Decision.APPROVE)

    content = _docx_bytes("This Privacy Policy describes how we process personal data.")
    resp = client.post(
        "/use-cases/with-document",
        data={"name": "Doc Use Case", "description": "desc", "owner": "team"},
        files={"file": ("policy.docx", content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )

    assert resp.status_code == 200
    body = resp.json()

    assert body["document"]["detected_language"] == "english"
    assert body["document"]["file_type"] == "docx"
    assert "Privacy Policy" in body["document"]["extracted_text"]

    assert body["report"]["decision"]["decision"] == "approve"
    assert "Privacy Policy" in body["report"]["use_case"]["documentation"]

    use_case_id = body["report"]["use_case"]["id"]
    audit_resp = client.get("/audit-log", params={"use_case_id": use_case_id})
    stages = [e["stage"] for e in audit_resp.json()]
    assert stages[0] == "document_processing"


def test_submit_use_case_with_document_rejects_unsupported_type(monkeypatch):
    _patch_agents(monkeypatch, decision=Decision.APPROVE)

    resp = client.post(
        "/use-cases/with-document",
        data={"name": "Doc Use Case", "description": "desc", "owner": "team"},
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )
    assert resp.status_code == 415


def test_get_missing_use_case_404():
    resp = client.get("/use-cases/does-not-exist")
    assert resp.status_code == 404


def test_approve_missing_use_case_404():
    resp = client.post(
        "/use-cases/does-not-exist/approve", json={"approved": True, "approver": "jane"}
    )
    assert resp.status_code == 404


def test_submit_use_case_agent_error_returns_502(monkeypatch):
    """A malformed structured response (e.g. a required field the model
    dropped) should surface as a 502 with the validation detail, not an
    opaque, bodyless 500."""

    def _raise(self, use_case):
        raise AgentError(
            "review_agent produced output that does not match ReviewResult: "
            "1 validation error for ReviewResult\nrationale\n  Field required"
        )

    monkeypatch.setattr("app.agents.risk_agent.RiskAssessmentAgent.assess", _raise)

    resp = client.post("/use-cases", json={"name": "Test", "description": "desc", "owner": "team"})
    assert resp.status_code == 502
    assert "does not match ReviewResult" in resp.json()["detail"]


def test_submit_use_case_openai_authentication_error_returns_502(monkeypatch):
    """An invalid/revoked OPENAI_API_KEY should surface as a 502 that names
    the key as the problem, not an opaque 500."""

    def _raise(self, use_case):
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        response = httpx.Response(401, request=request)
        raise openai.AuthenticationError("Incorrect API key provided", response=response, body=None)

    monkeypatch.setattr("app.agents.risk_agent.RiskAssessmentAgent.assess", _raise)

    resp = client.post("/use-cases", json={"name": "Test", "description": "desc", "owner": "team"})
    assert resp.status_code == 502
    assert "OPENAI_API_KEY" in resp.json()["detail"]


def test_submit_use_case_openai_error_returns_502(monkeypatch):
    """Any other OpenAI-side failure (rate limit, timeout, network) should
    also surface as a 502 instead of an opaque 500."""

    def _raise(self, use_case):
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        raise openai.APIConnectionError(request=request)

    monkeypatch.setattr("app.agents.risk_agent.RiskAssessmentAgent.assess", _raise)

    resp = client.post("/use-cases", json={"name": "Test", "description": "desc", "owner": "team"})
    assert resp.status_code == 502
    assert "OpenAI API request failed" in resp.json()["detail"]
