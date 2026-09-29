"""Tests for SDAIA evidence retrieval and citation resolution.

`app/rag/evidence.py` is the adapter between the RAG retriever and the Policy
Compliance Agent, and it carries the guarantee that matters most for a
governance report: a citation's source, page and quote always come from the
retrieval record, never from the model. These tests cover the adapter itself
and the agent's use of it - that retrieved passages really reach the prompt,
that an evidence id the model invents produces no citation, and that a
retrieval outage cannot fail a submission.

Retrieval is faked here, as it is for the whole suite (see
tests/conftest.py::fake_sdaia_retrieval); nothing in this file touches the
FAISS index or the embedding API.
"""
import json
import sys
from types import SimpleNamespace

import pytest

from app.agents.policy_agent import PolicyComplianceAgent
from app.models import AIUseCase, EvidenceCitation, RiskAssessmentResult, RiskLevel
from app.prompts import build_evidence_context
from app.rag import evidence as evidence_module
from app.rag.evidence import build_evidence_query, resolve_citations, retrieve_evidence


def _use_case(**overrides) -> AIUseCase:
    fields = {
        "name": "Cross-border support chatbot",
        "description": "Stores conversation transcripts outside the Kingdom.",
        "owner": "cx-platform",
        "data_classification": "personal data",
        "deployment_context": "customer-facing",
        "autonomy_level": "human-on-the-loop",
    }
    fields.update(overrides)
    return AIUseCase(**fields)


def _risk(**overrides) -> RiskAssessmentResult:
    fields = {
        "risk_level": RiskLevel.HIGH,
        "risk_score": 70,
        "risk_factors": ["cross-border transfer", "personal data"],
        "rationale": "Transcripts leave the Kingdom.",
    }
    fields.update(overrides)
    return RiskAssessmentResult(**fields)


def _retriever_hit(**overrides) -> dict:
    """One result in the shape app.rag.retriever.retrieve_policy_evidence
    returns."""
    hit = {
        "text": "Personal data must not be used as a decision criterion.",
        "title": "AI Ethics Principles",
        "file_name": "ai-principles.pdf",
        "page": 16,
        "domain": "ai_ethics",
        "audience": "all",
        "authority": "SDAIA",
        "distance_score": 0.31,
    }
    hit.update(overrides)
    return hit


@pytest.fixture
def fake_retriever(monkeypatch):
    """Stand in for the retriever modules without importing either.

    Both real modules load the FAISS index and build an embedding client, and
    `retrieve_evidence` imports one of them lazily depending on
    `USE_DIVERSITY_SELECTION` - so fake modules are placed in sys.modules under
    BOTH names. Patching only one leaves the other reachable, which is how the
    suite started making live embedding calls when the selection was switched.
    """

    def install(results=None, error=None):
        calls = []

        def retrieve_policy_evidence(query, k=5, **kwargs):
            calls.append({"query": query, "k": k})
            if error is not None:
                raise error
            return list(results or [])

        monkeypatch.setitem(
            sys.modules,
            "app.rag.retriever",
            SimpleNamespace(retrieve_policy_evidence=retrieve_policy_evidence),
        )
        # retriever_v2 exposes the same behaviour as `retrieve`, plus the
        # diagnostics the real module attaches.
        monkeypatch.setitem(
            sys.modules,
            "app.rag.retriever_v2",
            SimpleNamespace(
                retrieve=retrieve_policy_evidence,
                low_information_score=lambda text: 0.0,
            ),
        )
        return calls

    return install


# --- the retrieval query ----------------------------------------------------


def test_the_query_is_built_from_the_submitted_use_case():
    query = build_evidence_query(_use_case())

    assert "Cross-border support chatbot" in query
    assert "Stores conversation transcripts outside the Kingdom." in query
    assert "data classification: personal data" in query
    assert "deployment context: customer-facing" in query
    assert "autonomy level: human-on-the-loop" in query


def test_fields_the_submission_left_blank_are_left_out():
    query = build_evidence_query(
        _use_case(data_classification=None, deployment_context=None, autonomy_level=None)
    )

    assert "data classification" not in query
    assert "deployment context" not in query
    assert "autonomy level" not in query
    assert "Cross-border support chatbot" in query


def test_risk_factors_join_the_query_when_a_risk_assessment_exists():
    with_risk = build_evidence_query(_use_case(), _risk())
    without_risk = build_evidence_query(_use_case())

    assert "risk factors: cross-border transfer, personal data" in with_risk
    assert "risk factors" not in without_risk


def test_a_risk_assessment_without_factors_adds_nothing():
    query = build_evidence_query(_use_case(), _risk(risk_factors=[]))

    assert "risk factors" not in query


def test_the_query_never_carries_the_submitted_documentation():
    # Documentation can be a whole extracted PDF; it is not part of the
    # retrieval query, so a huge upload cannot blow up the embedding call.
    query = build_evidence_query(_use_case(documentation="SECRET-DPIA-CONTENTS"))

    assert "SECRET-DPIA-CONTENTS" not in query


# --- retrieval --------------------------------------------------------------


def test_passages_are_labelled_in_retrieval_order(fake_retriever):
    fake_retriever([_retriever_hit(page=16), _retriever_hit(page=44), _retriever_hit(page=7)])

    got = retrieve_evidence("cross-border personal data")

    assert [c["evidence_id"] for c in got] == ["E1", "E2", "E3"]
    assert [c["page"] for c in got] == [16, 44, 7]


def test_a_passage_carries_the_retrieval_record_fields(fake_retriever):
    fake_retriever([_retriever_hit()])

    (candidate,) = retrieve_evidence("personal data")

    assert candidate == {
        "evidence_id": "E1",
        "source": "ai-principles.pdf",
        "title": "AI Ethics Principles",
        "page": 16,
        "text": "Personal data must not be used as a decision criterion.",
    }


def test_top_k_is_passed_through_to_the_retriever(fake_retriever):
    calls = fake_retriever([_retriever_hit()])

    retrieve_evidence("personal data", k=3)

    assert calls == [{"query": "personal data", "k": 3}]


def test_the_default_is_the_accepted_top_5(fake_retriever):
    calls = fake_retriever([])

    retrieve_evidence("personal data")

    assert calls[0]["k"] == evidence_module.EVIDENCE_TOP_K == 5


@pytest.mark.parametrize("query", ["", "   ", "\n\t "])
def test_a_blank_query_retrieves_nothing_and_never_calls_the_retriever(fake_retriever, query):
    calls = fake_retriever([_retriever_hit()])

    assert retrieve_evidence(query) == []
    assert calls == []


def test_a_retrieval_failure_is_swallowed_so_a_submission_cannot_fail(fake_retriever, caplog):
    fake_retriever(error=RuntimeError("FAISS index missing"))

    with caplog.at_level("WARNING"):
        assert retrieve_evidence("personal data") == []

    assert any("retrieval unavailable" in r.getMessage() for r in caplog.records)


def test_a_retrieval_failure_is_logged_inside_the_platform_log_tree(fake_retriever, caplog):
    # app.observability.logging_config configures the `governai` tree only, so
    # a warning logged outside it never reaches the structured log.
    fake_retriever(error=RuntimeError("boom"))

    with caplog.at_level("WARNING"):
        retrieve_evidence("personal data")

    assert [r.name for r in caplog.records if "retrieval unavailable" in r.getMessage()] == [
        "governai.rag.evidence"
    ]


# --- citation resolution ----------------------------------------------------


def _candidates():
    return [
        {"evidence_id": "E1", "source": "a.pdf", "title": "A", "page": 1, "text": "first"},
        {"evidence_id": "E2", "source": "b.pdf", "title": "B", "page": 2, "text": "second"},
        {"evidence_id": "E3", "source": "c.pdf", "title": "C", "page": 3, "text": "third"},
    ]


def test_a_cited_id_resolves_to_the_retrieved_passage():
    citations, rejected = resolve_citations(["E2"], _candidates())

    assert citations == [
        {"evidence_id": "E2", "source": "b.pdf", "page": 2, "quote": "second"}
    ]
    assert rejected == []


def test_an_id_that_was_not_retrieved_produces_no_citation():
    citations, rejected = resolve_citations(["E9", "POL-003", "E1"], _candidates())

    assert [c["evidence_id"] for c in citations] == ["E1"]
    assert rejected == ["E9", "POL-003"]


def test_rejected_ids_are_logged(caplog):
    with caplog.at_level("WARNING"):
        resolve_citations(["E9"], _candidates())

    assert any("Dropped evidence ids" in r.getMessage() for r in caplog.records)


def test_citations_keep_retrieval_order_however_they_were_cited():
    citations, _ = resolve_citations(["E3", "E1", "E2"], _candidates())

    assert [c["evidence_id"] for c in citations] == ["E1", "E2", "E3"]


def test_an_id_cited_twice_yields_one_citation():
    citations, rejected = resolve_citations(["E2", "e2", " E2 "], _candidates())

    assert [c["evidence_id"] for c in citations] == ["E2"]
    assert rejected == []


def test_no_ids_cited_means_no_citations():
    assert resolve_citations([], _candidates()) == ([], [])
    assert resolve_citations(None, _candidates()) == ([], [])


def test_nothing_retrieved_means_every_cited_id_is_rejected():
    citations, rejected = resolve_citations(["E1"], [])

    assert citations == []
    assert rejected == ["E1"]


# --- the agent's use of retrieved evidence ---------------------------------


class RecordingClient:
    """Returns a fixed payload and records the messages it was sent."""

    def __init__(self, payload):
        self.messages = []
        message = SimpleNamespace(content=json.dumps(payload), tool_calls=None)
        completion = SimpleNamespace(choices=[SimpleNamespace(message=message)])

        def create(**kwargs):
            self.messages.append(kwargs["messages"])
            return completion

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    @property
    def prompt(self) -> str:
        return "\n".join(
            str(m.get("content", "")) for message in self.messages for m in message
        )


def _check(monkeypatch, payload=None, **overrides):
    full = {"status": "partially_compliant", "rationale": "r"}
    full.update(payload or {})
    client = RecordingClient(full)
    monkeypatch.setattr("app.agents.base.get_client", lambda: client)
    result = PolicyComplianceAgent().check(_use_case(**overrides), _risk())
    return result, client


def test_retrieved_passages_reach_the_policy_agent(monkeypatch, fake_sdaia_retrieval):
    fake_sdaia_retrieval.passages = [
        fake_sdaia_retrieval.passage("E1", page=16, text="Passage about decision criteria."),
        fake_sdaia_retrieval.passage("E2", page=44, source="pdpl.pdf", text="Passage about transfer."),
    ]

    _, client = _check(monkeypatch)

    assert "SDAIA RETRIEVED EVIDENCE" in client.prompt
    assert "[E1]" in client.prompt and "[E2]" in client.prompt
    assert "Passage about decision criteria." in client.prompt
    assert "Page: 44" in client.prompt
    assert "evidence_ids" in client.prompt  # the citation instruction


def test_the_agent_asks_for_the_query_built_from_the_submission(
    monkeypatch, fake_sdaia_retrieval
):
    _check(monkeypatch)

    (query,) = fake_sdaia_retrieval.queries
    assert "Cross-border support chatbot" in query
    assert "risk factors: cross-border transfer, personal data" in query


def test_no_evidence_block_when_nothing_was_retrieved(monkeypatch, fake_sdaia_retrieval):
    fake_sdaia_retrieval.passages = []

    result, client = _check(monkeypatch)

    assert "SDAIA RETRIEVED EVIDENCE" not in client.prompt
    assert result.evidence == []


def test_a_retrieval_outage_still_produces_a_compliance_result(
    monkeypatch, fake_sdaia_retrieval
):
    def unavailable(query, k=5):
        return []

    monkeypatch.setattr("app.agents.policy_agent.retrieve_evidence", unavailable)
    result, _ = _check(monkeypatch, {"satisfied_policies": ["POL-009"]})

    assert result.satisfied_policies == ["POL-009"]
    assert result.evidence == []


def test_a_cited_passage_becomes_a_citation_from_the_retrieval_record(
    monkeypatch, fake_sdaia_retrieval
):
    fake_sdaia_retrieval.passages = [
        fake_sdaia_retrieval.passage("E1", source="ai-principles.pdf", page=16, text="real quote")
    ]

    result, _ = _check(monkeypatch, {"evidence_ids": ["E1"]})

    assert [c.model_dump() for c in result.evidence] == [
        {"evidence_id": "E1", "source": "ai-principles.pdf", "page": 16, "quote": "real quote"}
    ]


def test_an_evidence_id_the_model_invented_produces_no_citation(
    monkeypatch, fake_sdaia_retrieval
):
    fake_sdaia_retrieval.passages = [fake_sdaia_retrieval.passage("E1")]

    result, _ = _check(monkeypatch, {"evidence_ids": ["E7"]})

    assert result.evidence == []
    # What the model claimed is still reported as-is, so the claim is auditable.
    assert result.evidence_ids == ["E7"]


def test_a_source_or_page_the_model_writes_itself_cannot_reach_a_citation(
    monkeypatch, fake_sdaia_retrieval
):
    fake_sdaia_retrieval.passages = [
        fake_sdaia_retrieval.passage("E1", source="ai-principles.pdf", page=16, text="real quote")
    ]

    result, _ = _check(
        monkeypatch,
        {
            "evidence_ids": ["E1"],
            "evidence": [
                {
                    "evidence_id": "E1",
                    "source": "invented-regulation.pdf",
                    "page": 999,
                    "quote": "a sentence that is not in the corpus",
                }
            ],
        },
    )

    (citation,) = result.evidence
    assert citation.source == "ai-principles.pdf"
    assert citation.page == 16
    assert citation.quote == "real quote"


def test_citations_are_evidence_citation_instances_not_dicts(
    monkeypatch, fake_sdaia_retrieval
):
    # model_copy(update=...) skips validation, so this is what keeps the
    # report's evidence a validated model rather than a raw dict.
    fake_sdaia_retrieval.passages = [fake_sdaia_retrieval.passage("E1")]
    result, _ = _check(monkeypatch, {"evidence_ids": ["E1"]})

    assert all(isinstance(c, EvidenceCitation) for c in result.evidence)
    assert json.loads(result.model_dump_json())["evidence"][0]["source"] == "ai-principles.pdf"


# --- how the passages are rendered ----------------------------------------


def test_the_evidence_block_says_so_when_nothing_was_retrieved():
    assert "no SDAIA passages were retrieved" in build_evidence_context([])


def test_a_rendered_passage_is_collapsed_onto_one_line():
    block = build_evidence_context(
        [{"evidence_id": "E1", "source": "a.pdf", "page": 3, "text": "line one\n\nline  two"}]
    )

    assert "Passage: line one line two" in block
    assert "Source: a.pdf" in block
    assert "Page: 3" in block
