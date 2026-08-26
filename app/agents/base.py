"""Base class implementing a small tool-calling agent loop against OpenAI.

Each concrete agent (Risk Assessment, Policy Compliance, Decision) supplies
a system prompt, a set of OpenAI-style tool schemas, and the Python
functions that back them. `BaseAgent.run` drives the chat-completions loop:
it lets the model call tools (policy repository, risk rules, document
analysis, audit log) as many times as it needs, then parses the model's
final answer into a typed pydantic response.
"""
import json
import re
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from app import config
from app.llm_client import get_client

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class AgentError(RuntimeError):
    """Raised when an agent fails to produce a valid structured response."""


def _extract_json(content: str) -> Dict[str, Any]:
    content = (content or "").strip()
    # Strip markdown code fences if the model wrapped its JSON in one.
    if content.startswith("```"):
        content = content.strip("`")
        content = re.sub(r"^json\s*", "", content, flags=re.IGNORECASE).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK_RE.search(content)
    if match:
        return json.loads(match.group(0))
    raise AgentError(f"Could not parse a JSON object out of model output: {content!r}")


class BaseAgent:
    """Shared tool-calling + structured-output loop for governance agents."""

    name: str = "base_agent"

    def __init__(
        self,
        system_prompt: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_functions: Optional[Dict[str, Callable[..., Any]]] = None,
        model: Optional[str] = None,
    ):
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.tool_functions = tool_functions or {}
        self.model = model or config.OPENAI_MODEL

    def run(
        self,
        user_message: str,
        response_model: Type[ResponseModel],
        max_tool_iterations: Optional[int] = None,
    ) -> ResponseModel:
        max_tool_iterations = max_tool_iterations or config.MAX_TOOL_ITERATIONS
        client = get_client()

        schema_hint = (
            "When you are ready to give your final answer, respond with ONLY a "
            "single raw JSON object (no markdown, no commentary) that matches "
            f"this JSON schema:\n{json.dumps(response_model.model_json_schema())}"
        )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": f"{self.system_prompt}\n\n{schema_hint}"},
            {"role": "user", "content": user_message},
        ]

        for _ in range(max_tool_iterations):
            kwargs: Dict[str, Any] = {"model": self.model, "messages": messages}
            if self.tools:
                kwargs["tools"] = self.tools
                kwargs["tool_choice"] = "auto"

            completion = client.chat.completions.create(**kwargs)
            message = completion.choices[0].message
            tool_calls = getattr(message, "tool_calls", None)

            if tool_calls:
                assistant_msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
                messages.append(assistant_msg)

                for tc in tool_calls:
                    fn = self.tool_functions.get(tc.function.name)
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    if fn is None:
                        result: Any = {"error": f"Unknown tool '{tc.function.name}'"}
                    else:
                        try:
                            result = fn(**args)
                        except Exception as exc:  # tool failures shouldn't crash the agent
                            result = {"error": str(exc)}
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(result, default=str),
                        }
                    )
                continue

            # No tool calls: this is the model's final answer.
            data = _extract_json(message.content or "")
            try:
                return response_model.model_validate(data)
            except ValidationError as exc:
                raise AgentError(
                    f"{self.name} produced output that does not match {response_model.__name__}: {exc}"
                ) from exc

        raise AgentError(f"{self.name} exceeded max_tool_iterations ({max_tool_iterations}) without a final answer")
