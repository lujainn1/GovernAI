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


def test_agent_raises_on_invalid_final_json(monkeypatch):
    fake_client = FakeClient([_completion(content="not json at all")])
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    agent = BaseAgent(system_prompt="test agent")
    with pytest.raises(AgentError):
        agent.run("go", EchoResult)


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
