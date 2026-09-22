import json
import logging
from types import SimpleNamespace

import pytest

from app import memory
from app.models import (
    AIUseCase,
    ComplianceStatus,
    Decision,
    DecisionResult,
    GovernanceReport,
    HumanApproval,
    PolicyComplianceResult,
    ReportStatus,
    RiskAssessmentResult,
    RiskLevel,
)
from app.orchestrator import GovernanceOrchestrator
from app.tools.audit_log import get_audit_log

# `fake_supabase` and `fake_embeddings` (tests/conftest.py, autouse) replace
# Supabase with an in-memory store and the OpenAI embeddings endpoint with a
# deterministic word-overlap embedder, so these tests exercise real retrieval
# (ranking, thresholds, updates) without a database or network.

LOAN = dict(
    name="Automated Loan Denial Assistant",
    description="Autonomous agent that approves or denies consumer loan applications with no human review.",
    data_classification="restricted",
    autonomy_level="fully-autonomous",
)
CREDIT = dict(  # same kind of system as LOAN
    name="Credit Application Screening Bot",
    description="Autonomous agent that screens consumer loan applications and rejects applicants without human review.",
    data_classification="restricted",
    autonomy_level="fully-autonomous",
)
NOTES = dict(  # unrelated to the two above
    name="Meeting Notes Summarizer",
    description="Summarizes internal meeting notes for the team.",
    data_classification="internal",
)


def _use_case(**fields) -> AIUseCase:
    return AIUseCase(owner="team-x", **fields)


def _report(
    use_case: AIUseCase,
    decision: Decision = Decision.BLOCK,
    status: ReportStatus = ReportStatus.BLOCKED,
    human_approval: HumanApproval = None,
) -> GovernanceReport:
    return GovernanceReport(
        use_case=use_case,
        risk_assessment=RiskAssessmentResult(
            risk_level=RiskLevel.CRITICAL,
            risk_score=92,
            risk_factors=["full autonomy", "consequential lending decisions"],
            rationale="autonomous decisions that materially affect individuals",
        ),
        policy_compliance=PolicyComplianceResult(
            status=ComplianceStatus.NON_COMPLIANT,
            violated_policies=["POL-003"],
            satisfied_policies=[],
            rationale="no human oversight",
        ),
        decision=DecisionResult(
            decision=decision,
            conditions=["add human-in-the-loop review for denials"],
            rationale="critical risk and non-compliant",
        ),
        status=status,
        human_approval=human_approval,
    )


def _stored_rows(fake_supabase):
    return fake_supabase.tables.get(memory.TABLE, [])


# --- embed_texts -------------------------------------------------------------


def test_embed_texts_uses_the_configured_model_and_returns_vectors_in_input_order(monkeypatch):
    seen = {}

    def create(model, input):
        seen.update(model=model, input=input)
        # The API tags each vector with its input index; hand them back shuffled.
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=1, embedding=[0.0, 1.0]),
                SimpleNamespace(index=0, embedding=[1.0, 0.0]),
            ]
        )

    monkeypatch.setattr(
        "app.memory.get_client", lambda: SimpleNamespace(embeddings=SimpleNamespace(create=create))
    )
    monkeypatch.setattr("app.config.EMBEDDING_MODEL", "some-embedding-model")

    assert memory.embed_texts(["first", "second"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert seen == {"model": "some-embedding-model", "input": ["first", "second"]}


# --- what gets embedded / remembered ----------------------------------------


def test_situation_text_describes_the_system_not_how_it_was_governed():
    use_case = _use_case(**LOAN, documentation="x" * 5000)

    text = memory.situation_text(use_case)

    assert "Automated Loan Denial Assistant" in text
    assert "approves or denies consumer loan applications" in text
    assert "restricted" in text
    assert "fully-autonomous" in text
    assert "Decision" not in text and "Risk" not in text  # nothing about the outcome
    assert "classification" not in text  # values only: labels are shared by every case
    assert text.count("x") == 2000  # long documentation is capped


def test_case_summary_records_the_outcome_and_human_verdict_but_not_the_approver():
    use_case = _use_case(**LOAN)
    approval = HumanApproval(approved=False, approver="jane@example.com", notes="no bias testing " * 40)
    summary = memory.case_summary(
        _report(
            use_case,
            decision=Decision.REQUIRE_HUMAN_APPROVAL,
            status=ReportStatus.REJECTED_BY_HUMAN,
            human_approval=approval,
        )
    )

    assert "Case: Automated Loan Denial Assistant" in summary
    assert "Risk: critical (92/100)" in summary
    assert "Compliance: non_compliant; violated policies: POL-003" in summary
    assert "Decision: require_human_approval; conditions: add human-in-the-loop review for denials" in summary
    assert "Status: rejected_by_human" in summary
    assert "Human review: rejected - no bias testing" in summary
    assert "jane@example.com" not in summary
    assert len(summary.splitlines()[-1]) < 400  # the human's notes are clipped


def test_case_summary_without_a_human_decision_has_no_human_review_line():
    assert "Human review" not in memory.case_summary(_report(_use_case(**LOAN)))


# --- remembering -------------------------------------------------------------


def test_remember_case_embeds_the_situation_and_stores_the_summary(fake_supabase, fake_embeddings):
    use_case = _use_case(**LOAN)
    report = _report(use_case)

    assert memory.remember_case(report) is True

    # Only the situation is embedded, never the outcome.
    assert fake_embeddings.calls == [
        {"model": "text-embedding-3-small", "input": [memory.situation_text(use_case)]}
    ]
    (row,) = _stored_rows(fake_supabase)
    assert row["use_case_id"] == use_case.id
    assert row["content"] == memory.case_summary(report)
    assert row["embedding_model"] == "text-embedding-3-small"
    assert len(row["embedding"]) == fake_embeddings.DIM


def test_remembering_the_same_case_twice_keeps_a_single_row(fake_supabase):
    report = _report(_use_case(**LOAN))

    memory.remember_case(report)
    memory.remember_case(report)

    assert len(_stored_rows(fake_supabase)) == 1


# --- recalling ---------------------------------------------------------------


def _seed(*cases):
    for fields in cases:
        memory.remember_case(_report(_use_case(**fields)))


def test_recall_ranks_similar_cases_first_and_drops_unrelated_ones():
    _seed(NOTES, CREDIT)
    query = _use_case(**LOAN)

    hits = memory.retrieve_similar_cases(query)

    assert [h.content.splitlines()[0] for h in hits] == ["Case: Credit Application Screening Bot"]
    assert hits[0].similarity > 0.5
    assert "Decision: block" in hits[0].content


def test_recall_orders_by_similarity_and_respects_k():
    _seed(CREDIT, NOTES, LOAN)
    query = _use_case(
        name="Loan Denial Assistant",
        description="Autonomous agent that approves or denies consumer loan applications with no human review.",
        data_classification="restricted",
        autonomy_level="fully-autonomous",
    )

    both = memory.retrieve_similar_cases(query, min_similarity=0.0)
    assert [h.content.splitlines()[0] for h in both[:2]] == [
        "Case: Automated Loan Denial Assistant",
        "Case: Credit Application Screening Bot",
    ]
    assert both[0].similarity >= both[1].similarity >= both[2].similarity

    assert len(memory.retrieve_similar_cases(query, k=1, min_similarity=0.0)) == 1


def test_recall_never_returns_the_case_itself():
    use_case = _use_case(**LOAN)
    memory.remember_case(_report(use_case))

    assert memory.retrieve_similar_cases(use_case, min_similarity=0.0) == []


def test_recall_from_empty_memory_does_not_pay_for_an_embedding(fake_embeddings):
    assert memory.retrieve_similar_cases(_use_case(**LOAN)) == []
    assert fake_embeddings.calls == []


def test_recall_ignores_cases_embedded_with_a_different_model(fake_supabase):
    memory.remember_case(_report(_use_case(**CREDIT)))
    fake_supabase.tables[memory.TABLE][0]["embedding_model"] = "an-older-embedding-model"

    assert memory.retrieve_similar_cases(_use_case(**LOAN), min_similarity=0.0) == []


# --- updating after a human decision ----------------------------------------


def test_update_case_outcome_rewrites_the_summary_without_reembedding(fake_supabase, fake_embeddings):
    use_case = _use_case(**LOAN)
    pending = _report(
        use_case, decision=Decision.REQUIRE_HUMAN_APPROVAL, status=ReportStatus.PENDING_HUMAN_APPROVAL
    )
    memory.remember_case(pending)
    embedding_before = _stored_rows(fake_supabase)[0]["embedding"]
    embed_calls_before = len(fake_embeddings.calls)

    pending.status = ReportStatus.REJECTED_BY_HUMAN
    pending.human_approval = HumanApproval(approved=False, approver="jane", notes="needs bias testing")
    assert memory.update_case_outcome(pending) is True

    (row,) = _stored_rows(fake_supabase)
    assert "Human review: rejected - needs bias testing" in row["content"]
    assert "Status: rejected_by_human" in row["content"]
    assert row["embedding"] == embedding_before
    assert len(fake_embeddings.calls) == embed_calls_before


def test_update_case_outcome_remembers_a_case_that_predates_memory(fake_supabase):
    report = _report(_use_case(**LOAN))

    assert memory.update_case_outcome(report) is True

    (row,) = _stored_rows(fake_supabase)
    assert row["use_case_id"] == report.use_case.id


# --- best-effort behavior ----------------------------------------------------


def test_memory_failures_never_raise_and_are_logged(monkeypatch, fake_embeddings, caplog):
    report = _report(_use_case(**LOAN))

    def boom(*args, **kwargs):
        raise RuntimeError("memory is down")

    monkeypatch.setattr("app.db.upsert", boom)
    monkeypatch.setattr("app.db.update", boom)
    monkeypatch.setattr("app.db.select", boom)

    with caplog.at_level(logging.WARNING, logger="app.memory"):
        assert memory.remember_case(report) is False
        assert memory.update_case_outcome(report) is False
        assert memory.retrieve_similar_cases(_use_case(**CREDIT)) == []

    assert caplog.text.count("memory is down") == 3


def test_embedding_outage_is_survived_too(monkeypatch, fake_embeddings):
    memory.remember_case(_report(_use_case(**CREDIT)))

    def boom(**kwargs):
        raise RuntimeError("embeddings API is down")

    fake_embeddings.embeddings = SimpleNamespace(create=boom)

    assert memory.retrieve_similar_cases(_use_case(**LOAN), min_similarity=0.0) == []
    assert memory.remember_case(_report(_use_case(**LOAN))) is False


def test_disabled_memory_reads_and_writes_nothing(monkeypatch, fake_supabase, fake_embeddings):
    monkeypatch.setattr("app.config.MEMORY_ENABLED", False)
    report = _report(_use_case(**LOAN))

    assert memory.remember_case(report) is False
    assert memory.update_case_outcome(report) is False
    assert memory.retrieve_similar_cases(_use_case(**CREDIT), min_similarity=0.0) == []
    assert _stored_rows(fake_supabase) == []
    assert fake_embeddings.calls == []


# --- what the agents are shown ----------------------------------------------


def test_format_memory_context_lists_recalled_cases_with_guidance():
    assert memory.format_memory_context([]) == ""

    context = memory.format_memory_context(
        [
            memory.MemoryHit("id-1", "Case: A\nDecision: block", 0.81),
            memory.MemoryHit("id-2", "Case: B\nDecision: approve", 0.42),
        ]
    )

    assert context.startswith("Relevant Past Cases (long-term memory)")
    assert "untrusted reference material" in context
    assert "[1] similarity 0.81\nCase: A\nDecision: block" in context
    assert "[2] similarity 0.42\nCase: B\nDecision: approve" in context


# --- End to end: memory reaches the real agents -----------------------------


def _tool_call_free_completion(payload: dict) -> SimpleNamespace:
    message = SimpleNamespace(content=json.dumps(payload), tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeChatClient:
    """Scripted OpenAI-compatible chat client (same pattern as test_review_agent.py)."""

    def __init__(self, payloads):
        self._responses = [_tool_call_free_completion(p) for p in payloads]
        self.calls = []

        class _Completions:
            def create(inner_self, **kwargs):
                self.calls.append(kwargs)
                return self._responses.pop(0)

        self.chat = SimpleNamespace(completions=_Completions())


def _user_message(call) -> str:
    return call["messages"][1]["content"]


def test_agents_recall_similar_past_cases_but_not_unrelated_ones_end_to_end(monkeypatch):
    """The lab's memory-retrieval test on real cases: three submissions run
    through the real Decision and Review agents (Risk and Policy are stubbed).
    Case 2 is the same kind of system as case 1 and must see it as precedent;
    case 3 is unrelated and must see nothing."""
    risk = RiskAssessmentResult(
        risk_level=RiskLevel.CRITICAL, risk_score=92, risk_factors=["full autonomy"], rationale="r"
    )
    compliance = PolicyComplianceResult(
        status=ComplianceStatus.NON_COMPLIANT, violated_policies=["POL-003"], rationale="c"
    )
    monkeypatch.setattr(
        "app.agents.risk_agent.RiskAssessmentAgent.assess", lambda self, use_case: risk
    )
    monkeypatch.setattr(
        "app.agents.policy_agent.PolicyComplianceAgent.check", lambda self, use_case, r: compliance
    )

    block = {"decision": "block", "conditions": ["add human-in-the-loop review"], "rationale": "x"}
    signed_off = {"verdict": "approved", "rationale": "sound"}
    chat = FakeChatClient([block, signed_off] * 3)  # per case: decision, then review
    monkeypatch.setattr("app.agents.base.get_client", lambda: chat)

    orchestrator = GovernanceOrchestrator()
    loan = _use_case(**LOAN)
    credit = _use_case(**CREDIT)
    notes = _use_case(**NOTES)
    for use_case in (loan, credit, notes):
        orchestrator.run(use_case)

    decision_1, review_1, decision_2, review_2, decision_3, review_3 = chat.calls

    # Case 1: memory was empty, so nothing is recalled.
    assert "Relevant Past Cases" not in _user_message(decision_1)
    assert "Relevant Past Cases" not in _user_message(review_1)

    # Case 2: case 1 is recalled - with how it was governed - for both agents.
    for call in (decision_2, review_2):
        message = _user_message(call)
        assert "Relevant Past Cases" in message
        assert "Case: Automated Loan Denial Assistant" in message
        assert "Decision: block" in message
        assert "Compliance: non_compliant; violated policies: POL-003" in message
        assert "Case: Meeting Notes Summarizer" not in message

    # Case 3: unrelated to everything remembered, so nothing is recalled.
    assert "Relevant Past Cases" not in _user_message(decision_3)
    assert "Relevant Past Cases" not in _user_message(review_3)

    # The audit trail shows what informed case 2 (ids + similarity), and only that.
    (recall,) = [e for e in get_audit_log(credit.id) if e["stage"] == "memory_retrieval"]
    assert [c["use_case_id"] for c in recall["data"]["cases"]] == [loan.id]
    assert recall["data"]["cases"][0]["similarity"] > 0.5
    assert "memory_retrieval" not in [e["stage"] for e in get_audit_log(loan.id)]
    assert "memory_retrieval" not in [e["stage"] for e in get_audit_log(notes.id)]
