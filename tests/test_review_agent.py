import json
from types import SimpleNamespace

from app.agents.decision_agent import DecisionAgent
from app.agents.review_agent import ReviewAgent
from app.models import (
    AIUseCase,
    ComplianceStatus,
    Decision,
    DecisionResult,
    PolicyComplianceResult,
    ReportStatus,
    ReviewResult,
    ReviewVerdict,
    RiskAssessmentResult,
    RiskLevel,
)
from app.orchestrator import GovernanceOrchestrator
from app.tools.audit_log import get_audit_log

# `fake_supabase` (tests/conftest.py, autouse) seeds the policies table from
# data/policies.yaml, so the reviewer's get_policies tool calls below run
# against the same policies the platform ships with.


def _use_case(**overrides) -> AIUseCase:
    defaults = dict(
        name="Automated Loan Denial Assistant",
        description="Fully autonomous agent approving or denying loans with no human review.",
        owner="lending-team",
        autonomy_level="fully-autonomous",
    )
    defaults.update(overrides)
    return AIUseCase(**defaults)


def _risk() -> RiskAssessmentResult:
    return RiskAssessmentResult(
        risk_level=RiskLevel.HIGH,
        risk_score=78,
        risk_factors=["full autonomy", "consequential lending decisions"],
        rationale="autonomous decisions that materially affect individuals",
    )


def _compliance() -> PolicyComplianceResult:
    return PolicyComplianceResult(
        status=ComplianceStatus.NON_COMPLIANT,
        violated_policies=["POL-003"],
        satisfied_policies=[],
        rationale="no human oversight of denial decisions",
    )


def _tool_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments))
    )


def _completion(content="", tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    """Same fake OpenAI-compatible client pattern used in test_base_agent.py
    and test_risk_agent.py: returns a scripted sequence of responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

        class _Completions:
            def create(inner_self, **kwargs):
                self.calls.append(kwargs)
                return self._responses.pop(0)

        self.chat = SimpleNamespace(completions=_Completions())


def _user_message(call) -> str:
    return call["messages"][1]["content"]


# --- ReviewAgent.review -----------------------------------------------------


def test_review_sends_the_whole_pipeline_output_to_the_model(monkeypatch):
    final = json.dumps({"verdict": "approved", "rationale": "consistent and actionable"})
    fake_client = FakeClient([_completion(content=final)])
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    decision = DecisionResult(
        decision=Decision.BLOCK,
        conditions=["add human-in-the-loop review for denials"],
        rationale="high risk and non-compliant",
    )
    result = ReviewAgent().review(_use_case(), _risk(), _compliance(), decision)

    assert isinstance(result, ReviewResult)
    assert result.verdict == ReviewVerdict.APPROVED
    assert result.issues == []
    assert result.suggestions == []

    sent = fake_client.calls[0]
    assert "Review Agent" in sent["messages"][0]["content"]
    user_message = _user_message(sent)
    assert "Automated Loan Denial Assistant" in user_message  # the use case
    assert "Risk score: 78/100" in user_message  # risk findings
    assert "POL-003" in user_message  # compliance findings
    assert "Decision (to be reviewed)" in user_message  # the decision under review
    assert "add human-in-the-loop review for denials" in user_message


def test_review_can_call_get_policies_to_verify_cited_policies(monkeypatch):
    final = json.dumps(
        {
            "verdict": "needs_revision",
            "issues": ["approve contradicts the non_compliant status"],
            "suggestions": ["require human approval and add the missing oversight condition"],
            "rationale": "a non-compliant, high-risk use case cannot be approved as-is",
        }
    )
    responses = [
        _completion(tool_calls=[_tool_call("call_1", "get_policies", {})]),
        _completion(content=final),
    ]
    fake_client = FakeClient(responses)
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    decision = DecisionResult(decision=Decision.APPROVE, conditions=[], rationale="looks fine")
    result = ReviewAgent().review(_use_case(), _risk(), _compliance(), decision)

    assert result.verdict == ReviewVerdict.NEEDS_REVISION
    assert result.issues == ["approve contradicts the non_compliant status"]
    assert len(result.suggestions) == 1

    assert len(fake_client.calls) == 2
    tool_messages = [m for m in fake_client.calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_messages  # the get_policies call was answered from the policy repository
    assert "POL-003" in tool_messages[0]["content"]


# --- Decision Agent revision input -----------------------------------------


def test_decision_agent_includes_reviewer_feedback_only_on_revision(monkeypatch):
    final = json.dumps(
        {"decision": "require_human_approval", "conditions": ["add human review"], "rationale": "x"}
    )
    fake_client = FakeClient([_completion(content=final), _completion(content=final)])
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    agent = DecisionAgent()
    previous = DecisionResult(decision=Decision.APPROVE, conditions=[], rationale="looks fine")
    review = ReviewResult(
        verdict=ReviewVerdict.NEEDS_REVISION,
        issues=["approve contradicts the non_compliant status"],
        suggestions=["escalate to a human"],
        rationale="not supported by the findings",
    )

    agent.decide(_use_case(), _risk(), _compliance())
    agent.decide(_use_case(), _risk(), _compliance(), previous_decision=previous, review=review)

    first, second = (_user_message(c) for c in fake_client.calls)
    assert "Reviewer Feedback" not in first

    assert "Reviewer Feedback" in second
    assert "approve contradicts the non_compliant status" in second  # the issue
    assert "escalate to a human" in second  # the suggestion
    assert "Decision: approve" in second  # its own previous answer


# --- End to end: Generator (Decision) -> Reviewer -> revision --------------


def test_review_loop_corrects_a_flawed_decision_end_to_end(monkeypatch):
    """The lab's generator -> reviewer -> revise flow on a real use case.

    Risk and Policy are stubbed (they're covered elsewhere); the Decision
    Agent and Review Agent are real and share one scripted OpenAI client, so
    the order of model calls is: decision, review, revised decision, review.
    The first decision is deliberately wrong - it approves a high-risk,
    non-compliant use case - and the reviewer catches it."""
    monkeypatch.setattr(
        "app.agents.risk_agent.RiskAssessmentAgent.assess", lambda self, use_case: _risk()
    )
    monkeypatch.setattr(
        "app.agents.policy_agent.PolicyComplianceAgent.check",
        lambda self, use_case, risk: _compliance(),
    )

    flawed_decision = {"decision": "approve", "conditions": [], "rationale": "internal pilot"}
    review_1 = {
        "verdict": "needs_revision",
        "issues": ["Approves a high-risk use case that the policy check found non-compliant"],
        "suggestions": ["Require human approval and add human oversight of denials as a condition"],
        "rationale": "The decision contradicts both the risk level and the compliance status.",
    }
    revised_decision = {
        "decision": "require_human_approval",
        "conditions": ["add human-in-the-loop review for every loan denial"],
        "rationale": "High risk and non-compliant; a person must confirm before go-live.",
    }
    review_2 = {"verdict": "approved", "rationale": "The revised decision addresses the issues."}

    fake_client = FakeClient(
        [_completion(content=json.dumps(x)) for x in (flawed_decision, review_1, revised_decision, review_2)]
    )
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    use_case = _use_case()
    report = GovernanceOrchestrator().run(use_case)

    # The review improved the final output: approve -> require_human_approval.
    assert report.decision.decision == Decision.REQUIRE_HUMAN_APPROVAL
    assert report.decision.conditions == ["add human-in-the-loop review for every loan denial"]
    assert report.status == ReportStatus.PENDING_HUMAN_APPROVAL

    # The revision was driven by the reviewer's feedback reaching the Decision Agent.
    assert len(fake_client.calls) == 4
    revision_input = _user_message(fake_client.calls[2])
    assert "Reviewer Feedback" in revision_input
    assert "Approves a high-risk use case that the policy check found non-compliant" in revision_input
    assert "Decision: approve" in revision_input

    entries = get_audit_log(use_case.id)
    assert [e["stage"] for e in entries] == [
        "intake",
        "risk_assessment",
        "policy_compliance",
        "decision",
        "review",
        "decision_revision",
        "review",
        "report_finalized",
    ]
    assert [e["actor"] for e in entries if e["stage"] == "review"] == ["review_agent"] * 2
