"""Step-by-step (human-in-the-loop) runs: a person approves or rejects every
agent's output before the next agent runs."""
import pytest
from fastapi.testclient import TestClient

from app.agents.base import AgentError
from app.api import app
from app.memory import MemoryHit
from app.models import (
    AIUseCase,
    ComplianceStatus,
    Decision,
    DecisionResult,
    PipelineAgent,
    PipelineRunStatus,
    PolicyComplianceResult,
    ReportStatus,
    ReviewResult,
    ReviewVerdict,
    RiskAssessmentResult,
    RiskLevel,
    StepStatus,
)
from app.orchestrator import (
    GovernanceOrchestrator,
    InvalidStepStateError,
    UseCaseNotFoundError,
)
from app.reports import load_report
from app.tools.audit_log import get_audit_log

client = TestClient(app)

RISK = RiskAssessmentResult(
    risk_level=RiskLevel.LOW, risk_score=20, risk_factors=["none"], rationale="low risk"
)
COMPLIANCE = PolicyComplianceResult(
    status=ComplianceStatus.COMPLIANT, violated_policies=[], satisfied_policies=[], rationale="ok"
)
APPROVE = DecisionResult(decision=Decision.APPROVE, conditions=[], rationale="fine")
SIGNED_OFF = ReviewResult(verdict=ReviewVerdict.APPROVED, rationale="consistent")
NEEDS_WORK = ReviewResult(
    verdict=ReviewVerdict.NEEDS_REVISION,
    issues=["conditions are vague"],
    suggestions=["make them concrete"],
    rationale="not actionable",
)


def _use_case(**overrides) -> AIUseCase:
    defaults = dict(
        name="Support Chat Assistant",
        description="Drafts replies for support agents",
        owner="team-x",
        autonomy_level="human-in-the-loop",
    )
    defaults.update(overrides)
    return AIUseCase(**defaults)


def _stub_agents(monkeypatch, reviews=(SIGNED_OFF,), decision=APPROVE):
    """Replace the four agents with stubs. Returns `calls`, the ordered list
    of agents that actually ran (plus the kwargs of each decide call).
    `reviews` are handed out in order; the last one repeats."""
    calls = []
    review_iter = iter(reviews)
    last_review = {"value": reviews[-1]}

    def _assess(self, use_case):
        calls.append(("risk", {}))
        return RISK

    def _check(self, use_case, risk):
        calls.append(("policy", {}))
        return COMPLIANCE

    def _decide(self, use_case, risk, compliance, **kwargs):
        calls.append(("decision", kwargs))
        return decision

    def _review(self, use_case, risk, compliance, decision):
        calls.append(("review", {}))
        return next(review_iter, last_review["value"])

    monkeypatch.setattr("app.agents.risk_agent.RiskAssessmentAgent.assess", _assess)
    monkeypatch.setattr("app.agents.policy_agent.PolicyComplianceAgent.check", _check)
    monkeypatch.setattr("app.agents.decision_agent.DecisionAgent.decide", _decide)
    monkeypatch.setattr("app.agents.review_agent.ReviewAgent.review", _review)
    return calls


def _approve(use_case_id, seq, approver="jane", notes=None):
    # A fresh orchestrator per call, as the API does: state must survive in storage.
    return GovernanceOrchestrator().decide_step(
        use_case_id, seq, approved=True, approver=approver, notes=notes
    )


def _agents(calls):
    return [name for name, _ in calls]


def test_start_runs_only_the_first_agent_and_waits_for_approval(monkeypatch):
    calls = _stub_agents(monkeypatch)
    use_case = _use_case()

    run = GovernanceOrchestrator().start_run(use_case)

    assert _agents(calls) == ["risk"]
    assert run.status == PipelineRunStatus.AWAITING_STEP_APPROVAL
    assert [(s.seq, s.agent, s.status) for s in run.steps] == [
        (1, PipelineAgent.RISK_ASSESSMENT, StepStatus.PENDING)
    ]
    assert run.steps[0].output["risk_level"] == "low"
    assert load_report(use_case.id) is None
    assert [e["stage"] for e in get_audit_log(use_case.id)] == ["intake", "risk_assessment"]


def test_each_approval_runs_exactly_the_next_agent(monkeypatch):
    calls = _stub_agents(monkeypatch)
    use_case = _use_case()
    GovernanceOrchestrator().start_run(use_case)

    run = _approve(use_case.id, 1)
    assert _agents(calls) == ["risk", "policy"]
    assert run.steps[-1].agent == PipelineAgent.POLICY_COMPLIANCE
    assert run.steps[-1].status == StepStatus.PENDING

    run = _approve(use_case.id, 2)
    assert _agents(calls) == ["risk", "policy", "decision"]

    run = _approve(use_case.id, 3)
    assert _agents(calls) == ["risk", "policy", "decision", "review"]
    assert run.status == PipelineRunStatus.AWAITING_STEP_APPROVAL
    assert load_report(use_case.id) is None  # the last step is still awaiting approval


def test_approving_the_last_step_produces_the_report_and_records_each_verdict(monkeypatch):
    _stub_agents(monkeypatch)
    use_case = _use_case()
    GovernanceOrchestrator().start_run(use_case)
    _approve(use_case.id, 1, approver="alice", notes="risk looks right")
    _approve(use_case.id, 2, approver="bob")
    _approve(use_case.id, 3, approver="carol")

    run = _approve(use_case.id, 4, approver="dave")

    assert run.status == PipelineRunStatus.COMPLETED
    assert [s.status for s in run.steps] == [StepStatus.APPROVED] * 4
    assert [s.decided_by for s in run.steps] == ["alice", "bob", "carol", "dave"]
    assert run.steps[0].notes == "risk looks right"
    assert run.steps[0].decided_at is not None

    # The same report `run` would produce: human-in-the-loop, so it still
    # ends at the final human sign-off.
    assert run.report is not None
    assert run.report.status == ReportStatus.PENDING_HUMAN_APPROVAL
    assert run.report.decision.decision == Decision.REQUIRE_HUMAN_APPROVAL
    assert load_report(use_case.id).status == ReportStatus.PENDING_HUMAN_APPROVAL

    assert [e["stage"] for e in get_audit_log(use_case.id)] == [
        "intake",
        "risk_assessment",
        "step_approval",
        "policy_compliance",
        "step_approval",
        "decision",
        "step_approval",
        "review",
        "step_approval",
        "human_in_the_loop_escalation",
        "report_finalized",
    ]

    # ...and the person can still make the final call, as before.
    decided = GovernanceOrchestrator().apply_human_decision(use_case.id, approved=True, approver="erin")
    assert decided.status == ReportStatus.APPROVED_BY_HUMAN


def test_step_approval_audit_entry_records_who_what_and_notes(monkeypatch):
    _stub_agents(monkeypatch)
    use_case = _use_case()
    GovernanceOrchestrator().start_run(use_case)
    _approve(use_case.id, 1, approver="alice@example.com", notes="looks right")

    (entry,) = [e for e in get_audit_log(use_case.id) if e["stage"] == "step_approval"]
    assert entry["actor"] == "alice@example.com"
    assert entry["data"] == {
        "step": 1,
        "agent": "risk_assessment",
        "revision": 0,
        "approved": True,
        "notes": "looks right",
    }


def test_rejecting_a_step_stops_the_run_and_no_later_agent_runs(monkeypatch):
    calls = _stub_agents(monkeypatch)
    use_case = _use_case()
    orchestrator = GovernanceOrchestrator()
    orchestrator.start_run(use_case)
    _approve(use_case.id, 1)

    run = orchestrator.decide_step(
        use_case.id, 2, approved=False, approver="jane", notes="wrong policies checked"
    )

    assert run.status == PipelineRunStatus.REJECTED
    assert [s.status for s in run.steps] == [StepStatus.APPROVED, StepStatus.REJECTED]
    assert run.steps[1].notes == "wrong policies checked"
    assert _agents(calls) == ["risk", "policy"]  # decision and review never ran
    assert load_report(use_case.id) is None

    rejection = get_audit_log(use_case.id)[-1]
    assert rejection["stage"] == "step_approval"
    assert rejection["data"]["approved"] is False

    with pytest.raises(InvalidStepStateError):
        _approve(use_case.id, 2)  # a stopped run can't be resumed


def test_only_the_pending_step_can_be_decided(monkeypatch):
    calls = _stub_agents(monkeypatch)
    use_case = _use_case()
    GovernanceOrchestrator().start_run(use_case)
    _approve(use_case.id, 1)

    # Step 1 was already approved (e.g. a double click); it must not approve step 2 unseen.
    with pytest.raises(InvalidStepStateError):
        _approve(use_case.id, 1)
    # Nor may a step that doesn't exist yet be approved in advance.
    with pytest.raises(InvalidStepStateError):
        _approve(use_case.id, 3)

    assert _agents(calls) == ["risk", "policy"]


def test_deciding_an_unknown_run_or_a_finished_run_fails(monkeypatch):
    _stub_agents(monkeypatch)
    with pytest.raises(UseCaseNotFoundError):
        _approve("does-not-exist", 1)

    use_case = _use_case()
    GovernanceOrchestrator().start_run(use_case)
    for seq in (1, 2, 3, 4):
        _approve(use_case.id, seq)
    with pytest.raises(InvalidStepStateError):
        _approve(use_case.id, 4)  # the run is already completed


def test_a_failing_agent_leaves_the_step_pending_so_it_can_be_approved_again(monkeypatch):
    _stub_agents(monkeypatch)
    use_case = _use_case()
    GovernanceOrchestrator().start_run(use_case)

    def _boom(self, use_case, risk):
        raise AgentError("policy agent produced invalid output")

    monkeypatch.setattr("app.agents.policy_agent.PolicyComplianceAgent.check", _boom)
    with pytest.raises(AgentError):
        _approve(use_case.id, 1)

    # Nothing was recorded: step 1 is still pending and nothing was logged for its approval.
    assert "step_approval" not in [e["stage"] for e in get_audit_log(use_case.id)]
    monkeypatch.setattr(
        "app.agents.policy_agent.PolicyComplianceAgent.check",
        lambda self, use_case, risk: COMPLIANCE,
    )
    run = _approve(use_case.id, 1)

    assert [s.seq for s in run.steps] == [1, 2]
    assert run.steps[0].status == StepStatus.APPROVED


def test_review_revision_becomes_extra_steps_a_person_also_approves(monkeypatch):
    calls = _stub_agents(monkeypatch, reviews=(NEEDS_WORK, SIGNED_OFF))
    use_case = _use_case()
    GovernanceOrchestrator().start_run(use_case)
    for seq in (1, 2, 3):
        _approve(use_case.id, seq)

    # Approving the review that asks for a revision sends the Decision back to work...
    run = _approve(use_case.id, 4)
    assert run.status == PipelineRunStatus.AWAITING_STEP_APPROVAL
    assert (run.steps[-1].agent, run.steps[-1].revision) == (PipelineAgent.DECISION, 1)
    _, kwargs = calls[-1]
    assert kwargs["previous_decision"] == APPROVE
    assert kwargs["review"] == NEEDS_WORK

    # ...and the revised decision is reviewed again, each step approved by a person.
    run = _approve(use_case.id, 5)
    assert (run.steps[-1].agent, run.steps[-1].revision) == (PipelineAgent.REVIEW, 1)
    run = _approve(use_case.id, 6)

    assert run.status == PipelineRunStatus.COMPLETED
    assert _agents(calls) == ["risk", "policy", "decision", "review", "decision", "review"]
    stages = [e["stage"] for e in get_audit_log(use_case.id)]
    assert "decision_revision" in stages
    assert "review_escalation" not in stages


def test_unresolved_review_after_the_revision_budget_escalates_an_approve(monkeypatch):
    # Not human-in-the-loop, so the only reason this ends pending is the escalation.
    _stub_agents(monkeypatch, reviews=(NEEDS_WORK,))
    use_case = _use_case(autonomy_level=None)
    GovernanceOrchestrator().start_run(use_case)

    for seq in range(1, 7):  # risk, policy, decision, review, decision (rev 1), review (rev 1)
        run = _approve(use_case.id, seq)

    assert run.status == PipelineRunStatus.COMPLETED
    assert run.report.status == ReportStatus.PENDING_HUMAN_APPROVAL
    assert run.report.decision.decision == Decision.REQUIRE_HUMAN_APPROVAL
    stages = [e["stage"] for e in get_audit_log(use_case.id)]
    assert "review_escalation" in stages
    assert "coordination_diagnosis" in stages


def test_a_step_by_step_run_of_an_autonomous_use_case_completes_without_a_final_sign_off(monkeypatch):
    _stub_agents(monkeypatch)
    use_case = _use_case(autonomy_level="fully-autonomous")
    GovernanceOrchestrator().start_run(use_case)
    for seq in (1, 2, 3):
        _approve(use_case.id, seq)

    run = _approve(use_case.id, 4)

    assert run.report.status == ReportStatus.COMPLETED
    assert run.report.decision.decision == Decision.APPROVE


def test_the_recalled_memory_context_is_kept_for_every_later_agent(monkeypatch):
    _stub_agents(monkeypatch)
    seen = {}

    hit = MemoryHit(use_case_id="past-case", content="Case: earlier", similarity=0.9)
    monkeypatch.setattr("app.orchestrator.retrieve_similar_cases", lambda use_case: [hit])
    monkeypatch.setattr(
        "app.orchestrator.format_memory_context", lambda hits: "Relevant Past Cases: precedent"
    )

    def _check(self, use_case, risk):
        seen["policy"] = self.memory_context
        return COMPLIANCE

    def _review(self, use_case, risk, compliance, decision):
        seen["review"] = self.memory_context
        return SIGNED_OFF

    monkeypatch.setattr("app.agents.policy_agent.PolicyComplianceAgent.check", _check)
    monkeypatch.setattr("app.agents.review_agent.ReviewAgent.review", _review)

    use_case = _use_case()
    GovernanceOrchestrator().start_run(use_case)
    for seq in (1, 2, 3):
        _approve(use_case.id, seq)  # each call is a brand-new orchestrator

    assert seen == {
        "policy": "Relevant Past Cases: precedent",
        "review": "Relevant Past Cases: precedent",
    }


# =========================================================
# HTTP API
# =========================================================

BODY = {
    "name": "Support Chat Assistant",
    "description": "Drafts replies for support agents",
    "owner": "team-x",
    "autonomy_level": "human-in-the-loop",
}


def _decision_url(use_case_id, seq):
    return f"/pipeline-runs/{use_case_id}/steps/{seq}/decision"


def test_api_walks_a_run_from_start_to_report(monkeypatch):
    _stub_agents(monkeypatch)

    started = client.post("/pipeline-runs", json=BODY)
    assert started.status_code == 200
    run = started.json()
    use_case_id = run["use_case"]["id"]
    assert run["status"] == "awaiting_step_approval"
    assert [(s["seq"], s["agent"], s["status"]) for s in run["steps"]] == [
        (1, "risk_assessment", "pending")
    ]
    assert run["report"] is None
    assert "memory_context" not in run  # internal, never sent to clients

    for seq, next_agent in [(1, "policy_compliance"), (2, "decision"), (3, "review")]:
        resp = client.post(_decision_url(use_case_id, seq), json={"approved": True, "notes": "ok"})
        assert resp.status_code == 200
        assert resp.json()["steps"][-1]["agent"] == next_agent

    final = client.post(_decision_url(use_case_id, 4), json={"approved": True})
    assert final.status_code == 200
    assert final.json()["status"] == "completed"
    assert final.json()["report"]["status"] == "pending_human_approval"
    # The approver is the signed-in user (see conftest.bypass_auth), not the client.
    assert final.json()["steps"][0]["decided_by"] == "test@example.com"

    fetched = client.get(f"/pipeline-runs/{use_case_id}")
    assert fetched.status_code == 200
    assert fetched.json()["report"]["use_case"]["id"] == use_case_id


def test_api_reject_stops_the_run(monkeypatch):
    _stub_agents(monkeypatch)
    use_case_id = client.post("/pipeline-runs", json=BODY).json()["use_case"]["id"]

    resp = client.post(_decision_url(use_case_id, 1), json={"approved": False, "notes": "no"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert resp.json()["steps"][0]["status"] == "rejected"
    assert client.post(_decision_url(use_case_id, 1), json={"approved": True}).status_code == 409


def test_api_rejects_stale_and_unknown_decisions(monkeypatch):
    _stub_agents(monkeypatch)
    use_case_id = client.post("/pipeline-runs", json=BODY).json()["use_case"]["id"]
    client.post(_decision_url(use_case_id, 1), json={"approved": True})

    assert client.post(_decision_url(use_case_id, 1), json={"approved": True}).status_code == 409
    assert client.post(_decision_url("does-not-exist", 1), json={"approved": True}).status_code == 404
    assert client.get("/pipeline-runs/does-not-exist").status_code == 404


def test_api_lists_runs_optionally_filtered_by_status(monkeypatch):
    _stub_agents(monkeypatch)
    waiting = client.post("/pipeline-runs", json=BODY).json()["use_case"]["id"]
    stopped = client.post("/pipeline-runs", json={**BODY, "name": "Other"}).json()["use_case"]["id"]
    client.post(_decision_url(stopped, 1), json={"approved": False})

    everything = client.get("/pipeline-runs").json()
    assert {r["use_case"]["id"] for r in everything} == {waiting, stopped}

    only_waiting = client.get("/pipeline-runs", params={"status": "awaiting_step_approval"}).json()
    assert [r["use_case"]["id"] for r in only_waiting] == [waiting]
    assert client.get("/pipeline-runs", params={"status": "bogus"}).status_code == 422


def test_api_agent_failure_on_advance_is_a_502_and_the_step_stays_pending(monkeypatch):
    _stub_agents(monkeypatch)
    use_case_id = client.post("/pipeline-runs", json=BODY).json()["use_case"]["id"]

    def _boom(self, use_case, risk):
        raise AgentError("policy agent produced invalid output")

    monkeypatch.setattr("app.agents.policy_agent.PolicyComplianceAgent.check", _boom)
    failed = client.post(_decision_url(use_case_id, 1), json={"approved": True})

    assert failed.status_code == 502
    assert "invalid output" in failed.json()["detail"]
    run = client.get(f"/pipeline-runs/{use_case_id}").json()
    assert [s["status"] for s in run["steps"]] == ["pending"]
