"""Regression tests for two defects found validating the Preview build.

Both are provider-neutral: they concern JSON parsing and PostgREST error
translation, not any particular model or vendor.

1. A model reply that is a complete JSON object FOLLOWED BY more text raised a
   bare JSONDecodeError out of `_extract_json`, escaping the AgentError ->
   502 path and surfacing as an unhandled HTTP 500. Observed live with GPT
   (gpt-4o-mini) on 1 of 3 governance submissions:
       risk_assessment_failed: JSONDecodeError
       "Extra data: line 1 column 1021 (char 1020)"

2. `GET /use-cases/<non-uuid>` returned HTTP 500. PostgREST rejects a
   non-castable filter value with 400 / code 22P02, which is a client error,
   not a database outage, and the route already intends a 404.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from app.agents.base import AgentError, _extract_json
from app.api import app
from app.reports import load_report

client = TestClient(app, raise_server_exceptions=False)

VALID = json.dumps(
    {"risk_level": "low", "risk_score": 10, "risk_factors": ["none"], "rationale": "r"}
)


# --- 1. JSON extraction must be total -------------------------------------


def test_object_followed_by_prose_is_parsed():
    """The exact live failure: a complete object, then more model text."""
    assert _extract_json(VALID + "\n\nNote: I also checked POL-003.")["risk_level"] == "low"


def test_two_concatenated_objects_take_the_first():
    assert _extract_json(VALID + VALID)["risk_score"] == 10


def test_prose_before_the_object_is_parsed():
    assert _extract_json("Here is my answer: " + VALID)["risk_level"] == "low"


def test_a_fenced_object_is_still_parsed():
    assert _extract_json("```json\n" + VALID + "\n```")["risk_level"] == "low"


def test_a_plain_object_is_still_parsed():
    assert _extract_json(VALID)["risk_level"] == "low"


@pytest.mark.parametrize(
    "content",
    ["not json at all", '{"a": ', "", "[1, 2, 3]", "{{{", '"a string"', "null", "42"],
)
def test_unparseable_output_raises_agent_error_never_json_decode_error(content):
    """One failure type for callers: AgentError, which app.api maps to 502."""
    with pytest.raises(AgentError):
        _extract_json(content)


def test_the_error_message_is_still_truncated():
    """Unchanged behaviour: the message is persisted with the agent run, so it
    stays a short preview rather than the whole reply."""
    with pytest.raises(AgentError) as caught:
        _extract_json("x" * 5000)

    assert len(str(caught.value)) < 1000


# --- 2. a non-UUID id is a client error, not a server error ---------------


def _invalid_uuid_error():
    request = httpx.Request("GET", "https://db.example/rest/v1/use_cases")
    return httpx.HTTPStatusError(
        "invalid input syntax",
        request=request,
        response=httpx.Response(
            400,
            request=request,
            json={"code": "22P02", "message": "invalid input syntax for type uuid"},
        ),
    )


def _other_db_error():
    request = httpx.Request("GET", "https://db.example/rest/v1/use_cases")
    return httpx.HTTPStatusError(
        "server error",
        request=request,
        response=httpx.Response(500, request=request, json={"code": "XX000"}),
    )


def test_invalid_value_error_is_recognised():
    assert db.is_invalid_value_error(_invalid_uuid_error()) is True
    assert db.is_invalid_value_error(_other_db_error()) is False


def test_load_report_treats_a_non_uuid_id_as_not_found(monkeypatch):
    monkeypatch.setattr(db, "select", lambda *a, **k: (_ for _ in ()).throw(_invalid_uuid_error()))

    assert load_report("does-not-exist") is None


def test_load_report_still_propagates_real_database_errors(monkeypatch):
    """The fix must not swallow genuine database failures."""
    monkeypatch.setattr(db, "select", lambda *a, **k: (_ for _ in ()).throw(_other_db_error()))

    with pytest.raises(httpx.HTTPStatusError):
        load_report("does-not-exist")


def test_get_use_case_returns_404_for_a_non_uuid_id(monkeypatch):
    monkeypatch.setattr(db, "select", lambda *a, **k: (_ for _ in ()).throw(_invalid_uuid_error()))
    response = client.get("/use-cases/does-not-exist")

    assert response.status_code == 404
    assert response.json()["detail"] == "Use case not found"


def test_approve_returns_404_for_a_non_uuid_id(monkeypatch):
    monkeypatch.setattr(db, "select", lambda *a, **k: (_ for _ in ()).throw(_invalid_uuid_error()))
    response = client.post(
        "/use-cases/does-not-exist/approve", json={"approved": True, "approver": "a"}
    )

    assert response.status_code == 404


# --- 3. one structured-output repair retry --------------------------------
# A live GPT run lost a whole submission when the Review Agent returned a
# ReviewResult with no `rationale`: review_failed -> AgentError -> HTTP 502.
# The model now gets exactly one chance to restate the same findings in the
# right shape. A second failure still propagates unchanged.

import logging
from types import SimpleNamespace

from app.agents.base import BaseAgent
from app.models import ReviewResult, ReviewVerdict


def _completion(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content, tool_calls=None),
            finish_reason="stop",
        )],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


class ScriptedClient:
    def __init__(self, *contents):
        self._contents = list(contents)
        self.calls = []

        class _Completions:
            def create(inner, **kwargs):
                self.calls.append(kwargs)
                return _completion(self._contents.pop(0))

        self.chat = SimpleNamespace(completions=_Completions())


GOOD_REVIEW = json.dumps(
    {"verdict": "approved", "issues": [], "suggestions": [], "rationale": "Consistent."}
)
# The exact shape that failed live: a valid verdict, no `rationale`.
MISSING_FIELD = json.dumps({"verdict": "needs_revision", "issues": ["Conditions are vague."]})


def test_missing_required_field_is_repaired_on_one_retry(monkeypatch):
    scripted = ScriptedClient(MISSING_FIELD, GOOD_REVIEW)
    monkeypatch.setattr("app.agents.base.get_client", lambda: scripted)

    result = BaseAgent(system_prompt="s").run("m", ReviewResult)

    assert result.verdict is ReviewVerdict.APPROVED
    assert len(scripted.calls) == 2


def test_the_repair_prompt_asks_only_for_the_format(monkeypatch):
    scripted = ScriptedClient(MISSING_FIELD, GOOD_REVIEW)
    monkeypatch.setattr("app.agents.base.get_client", lambda: scripted)
    BaseAgent(system_prompt="s").run("m", ReviewResult)

    repair = scripted.calls[1]["messages"][-1]
    assert repair["role"] == "user"
    assert "could not be used" in repair["content"]
    assert "Do not change" in repair["content"]
    # the model's own failed answer is in the transcript so it can correct it
    assert scripted.calls[1]["messages"][-2]["content"] == MISSING_FIELD


def test_unparseable_output_is_also_repaired(monkeypatch):
    scripted = ScriptedClient("I cannot answer that.", GOOD_REVIEW)
    monkeypatch.setattr("app.agents.base.get_client", lambda: scripted)

    assert BaseAgent(system_prompt="s").run("m", ReviewResult).verdict is ReviewVerdict.APPROVED


def test_a_persistent_failure_is_not_hidden(monkeypatch):
    scripted = ScriptedClient(MISSING_FIELD, MISSING_FIELD)
    monkeypatch.setattr("app.agents.base.get_client", lambda: scripted)

    with pytest.raises(AgentError, match="does not match ReviewResult"):
        BaseAgent(system_prompt="s").run("m", ReviewResult)

    assert len(scripted.calls) == 2  # exactly one retry, never a loop


def test_repair_is_offered_once_per_run_only(monkeypatch):
    scripted = ScriptedClient(MISSING_FIELD, MISSING_FIELD, GOOD_REVIEW)
    monkeypatch.setattr("app.agents.base.get_client", lambda: scripted)

    with pytest.raises(AgentError):
        BaseAgent(system_prompt="s").run("m", ReviewResult)

    assert len(scripted.calls) == 2  # the third scripted reply is never requested


def test_a_valid_first_answer_costs_no_extra_call(monkeypatch):
    scripted = ScriptedClient(GOOD_REVIEW)
    monkeypatch.setattr("app.agents.base.get_client", lambda: scripted)

    assert BaseAgent(system_prompt="s").run("m", ReviewResult).verdict is ReviewVerdict.APPROVED
    assert len(scripted.calls) == 1


def test_the_repair_is_observable(monkeypatch, caplog):
    scripted = ScriptedClient(MISSING_FIELD, GOOD_REVIEW)
    monkeypatch.setattr("app.agents.base.get_client", lambda: scripted)

    with caplog.at_level(logging.WARNING, logger="app.agents.base"):
        BaseAgent(system_prompt="s").run("m", ReviewResult)

    assert any(r.getMessage() == "output_repair" for r in caplog.records)


def test_the_retry_still_counts_toward_the_trace(monkeypatch):
    """Observability must see the extra round-trip, not hide it."""
    scripted = ScriptedClient(MISSING_FIELD, GOOD_REVIEW)
    monkeypatch.setattr("app.agents.base.get_client", lambda: scripted)

    agent = BaseAgent(system_prompt="s")
    agent.run("m", ReviewResult)

    assert agent.last_trace["iterations"] == 2
