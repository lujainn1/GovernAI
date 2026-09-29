import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.agents.base import AgentError, BaseAgent


class EchoResult(BaseModel):
    value: int
    note: str


def _tool_call(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _completion(content: str = "", tool_calls=None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    """Fake OpenAI-compatible client that returns a scripted sequence of
    chat.completions.create() responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

        class _Completions:
            def create(inner_self, **kwargs):
                self.calls.append(kwargs)
                return self._responses.pop(0)

        self.chat = SimpleNamespace(completions=_Completions())


def test_agent_calls_tool_then_returns_final_json(monkeypatch):
    seen_args = {}

    def echo_tool(x: int):
        seen_args["x"] = x
        return {"doubled": x * 2}

    responses = [
        _completion(tool_calls=[_tool_call("call_1", "echo_tool", {"x": 21})]),
        _completion(content=json.dumps({"value": 42, "note": "ok"})),
    ]
    fake_client = FakeClient(responses)
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    agent = BaseAgent(
        system_prompt="test agent",
        tools=[{"type": "function", "function": {"name": "echo_tool", "parameters": {}}}],
        tool_functions={"echo_tool": echo_tool},
    )
    result = agent.run("do the thing", EchoResult)

    assert isinstance(result, EchoResult)
    assert result.value == 42
    assert seen_args["x"] == 21
    assert len(fake_client.calls) == 2
    # second call must include the tool result message
    tool_messages = [m for m in fake_client.calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_messages
    assert json.loads(tool_messages[0]["content"]) == {"doubled": 42}


def test_agent_parses_json_wrapped_in_markdown_fence(monkeypatch):
    content = "```json\n" + json.dumps({"value": 7, "note": "fenced"}) + "\n```"
    fake_client = FakeClient([_completion(content=content)])
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    agent = BaseAgent(system_prompt="test agent")
    result = agent.run("go", EchoResult)
    assert result.value == 7
    assert result.note == "fenced"


def test_agent_parses_json_followed_by_trailing_prose(monkeypatch):
    """The model answered with its JSON object and then kept talking. Spanning
    from the first "{" to the last "}" swallowed the trailing sentence and the
    whole thing failed to parse as "Extra data", surfacing as a 500."""
    content = (
        json.dumps({"value": 7, "note": "answer"})
        + "\n\nLet me know if you want the scoring broken down {per rule}."
    )
    fake_client = FakeClient([_completion(content=content)])
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    agent = BaseAgent(system_prompt="test agent")
    result = agent.run("go", EchoResult)
    assert result.value == 7
    assert result.note == "answer"


def test_agent_parses_json_preceded_by_prose_containing_braces(monkeypatch):
    content = (
        "Here is the assessment for the set {alpha, beta}:\n"
        + json.dumps({"value": 3, "note": "after prose"})
    )
    fake_client = FakeClient([_completion(content=content)])
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    agent = BaseAgent(system_prompt="test agent")
    result = agent.run("go", EchoResult)
    assert result.value == 3
    assert result.note == "after prose"


def test_agent_raises_agent_error_not_json_decode_error(monkeypatch):
    """Unparseable output must arrive as AgentError, which the API maps to a
    502 with a readable message - a bare JSONDecodeError escapes as a 500.

    Truncated output is offered the same one correction as a schema-invalid
    answer, so it takes two bad replies to reach the real failure."""
    fake_client = FakeClient(
        [
            _completion(content='{"value": 1, "note": '),
            _completion(content='{"value": 1, "note": '),
        ]
    )
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    agent = BaseAgent(system_prompt="test agent")
    with pytest.raises(AgentError):
        agent.run("go", EchoResult)


def test_agent_raises_on_invalid_final_json(monkeypatch):
    # The agent asks once for a correction (see BaseAgent._run_loop), so output
    # that is still unusable after that retry is the real failure.
    fake_client = FakeClient(
        [_completion(content="not json at all"), _completion(content="still not json")]
    )
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    agent = BaseAgent(system_prompt="test agent")
    with pytest.raises(AgentError):
        agent.run("go", EchoResult)

    assert len(fake_client.calls) == 2


def test_agent_raises_after_max_tool_iterations(monkeypatch):
    def noop_tool():
        return {}

    # Always returns a tool call, never a final answer -> should exceed the cap.
    responses = [_completion(tool_calls=[_tool_call(f"call_{i}", "noop_tool", {})]) for i in range(10)]
    fake_client = FakeClient(responses)
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    agent = BaseAgent(
        system_prompt="test agent",
        tools=[{"type": "function", "function": {"name": "noop_tool", "parameters": {}}}],
        tool_functions={"noop_tool": noop_tool},
    )
    with pytest.raises(AgentError):
        agent.run("go", EchoResult, max_tool_iterations=3)


def test_agent_appends_memory_context_to_the_user_message(monkeypatch):
    final = json.dumps({"value": 1, "note": "ok"})
    fake_client = FakeClient([_completion(content=final), _completion(content=final)])
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    agent = BaseAgent(system_prompt="test agent")
    agent.run("go", EchoResult)  # no memory: the input is sent untouched

    agent.memory_context = "Relevant Past Cases (long-term memory)\n[1] similarity 0.80"
    agent.run("go", EchoResult)

    sent = [call["messages"][1]["content"] for call in fake_client.calls]
    assert sent[0] == "go"
    assert sent[1] == "go\n\nRelevant Past Cases (long-term memory)\n[1] similarity 0.80"


def test_agent_repairs_a_missing_required_field(monkeypatch):
    """Valid JSON that drops a required field used to raise immediately, losing
    the whole pipeline run over one absent string (the real case: review_agent
    returning verdict/issues/suggestions with no rationale). The agent is now
    handed the problem and asked to correct it."""
    responses = [
        _completion(content=json.dumps({"value": 1})),  # "note" missing
        _completion(content=json.dumps({"value": 1, "note": "fixed"})),
    ]
    fake_client = FakeClient(responses)
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    agent = BaseAgent(system_prompt="test agent")
    result = agent.run("go", EchoResult)

    assert result.value == 1
    assert result.note == "fixed"
    assert len(fake_client.calls) == 2
    assert agent.last_trace["schema_repairs"] == 1
    # The correction names the offending field rather than just "invalid".
    repair_prompt = fake_client.calls[1]["messages"][-1]["content"]
    assert "note" in repair_prompt


def test_agent_gives_up_once_the_repair_budget_is_spent(monkeypatch):
    monkeypatch.setattr("app.config.MAX_SCHEMA_REPAIRS", 1)
    responses = [
        _completion(content=json.dumps({"value": 1})),
        _completion(content=json.dumps({"value": 2})),
    ]
    fake_client = FakeClient(responses)
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    agent = BaseAgent(system_prompt="test agent")
    with pytest.raises(AgentError, match="does not match EchoResult"):
        agent.run("go", EchoResult)

    assert len(fake_client.calls) == 2
    assert agent.last_trace["schema_repairs"] == 1


def test_agent_does_not_repair_when_budget_is_zero(monkeypatch):
    """0 keeps the original behaviour: fail on the first invalid answer."""
    monkeypatch.setattr("app.config.MAX_SCHEMA_REPAIRS", 0)
    fake_client = FakeClient([_completion(content=json.dumps({"value": 1}))])
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    agent = BaseAgent(system_prompt="test agent")
    with pytest.raises(AgentError):
        agent.run("go", EchoResult)

    assert len(fake_client.calls) == 1
    assert agent.last_trace["schema_repairs"] == 0


def test_repair_keeps_the_tool_results_already_gathered(monkeypatch):
    """The repair must continue the conversation, not restart it - otherwise it
    throws away the tool work that made the answer expensive."""

    def echo_tool(x: int):
        return {"doubled": x * 2}

    responses = [
        _completion(tool_calls=[_tool_call("call_1", "echo_tool", {"x": 21})]),
        _completion(content=json.dumps({"value": 42})),  # "note" missing
        _completion(content=json.dumps({"value": 42, "note": "ok"})),
    ]
    fake_client = FakeClient(responses)
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    agent = BaseAgent(
        system_prompt="test agent",
        tools=[{"type": "function", "function": {"name": "echo_tool", "parameters": {}}}],
        tool_functions={"echo_tool": echo_tool},
    )
    result = agent.run("go", EchoResult)

    assert result.note == "ok"
    final_messages = fake_client.calls[-1]["messages"]
    tool_results = [m for m in final_messages if m.get("role") == "tool"]
    assert len(tool_results) == 1
    assert "doubled" in tool_results[0]["content"]
