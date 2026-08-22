from fastapi.testclient import TestClient

from app.api import app
from app.models import Decision, DecisionResult, PolicyComplianceResult, ComplianceStatus, RiskAssessmentResult, RiskLevel

client = TestClient(app)


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
        lambda self, uc, risk, compliance: decision_result,
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


def test_get_missing_use_case_404():
    resp = client.get("/use-cases/does-not-exist")
    assert resp.status_code == 404


def test_approve_missing_use_case_404():
    resp = client.post(
        "/use-cases/does-not-exist/approve", json={"approved": True, "approver": "jane"}
    )
    assert resp.status_code == 404
