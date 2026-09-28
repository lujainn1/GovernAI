"""Base class implementing a small tool-calling agent loop against OpenAI.

Each concrete agent (Risk Assessment, Policy Compliance, Decision) supplies
a system prompt, a set of OpenAI-style tool schemas, and the Python
functions that back them. `BaseAgent.run` drives the chat-completions loop:
it lets the model call tools (policy repository, risk rules, document
analysis, audit log) as many times as it needs, then parses the model's
final answer into a typed pydantic response.
"""
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from app import config
from app.evaluation.checks import tool_succeeded
from app.llm_client import get_client
from app.models import utcnow_iso
from app.observability.logging_config import emit

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)

logger = logging.getLogger("governai.agent")

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_ERROR_PREVIEW_CHARS = 500
_OUTPUT_PREVIEW_CHARS = 120


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
    # Only a short preview: this message is persisted with the agent run.
    raise AgentError(f"Could not parse a JSON object out of model output: {content[:_OUTPUT_PREVIEW_CHARS]!r}")


def _new_trace(model: Optional[str] = None) -> Dict[str, Any]:
    return {
        "model": model,
        "started_at": None,  # set when run() starts; None means the agent never ran
        "latency_ms": 0.0,
        "iterations": 0,  # model round-trips
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "steps": [],  # ordered llm / tool steps, each with its own latency
        "tool_calls": [],
        "error": None,  # {"type", "message"} if run() raised
    }


def _ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


class BaseAgent:
    """Shared tool-calling + structured-output loop for governance agents.

    Every `run` records what the agent observably did in `last_trace`:
    the ordered steps (each model call and tool call, with its latency),
    prompt/completion token counts, each tool call's arguments, result and
    outcome, and the error if the run failed. app.observability persists it
    as an `agent_runs` row and app.evaluation reads it to judge behavior,
    not just the final answer. The trace is reset at the start of each run
    and filled in as the loop progresses, so it is still available for
    inspection when `run` raises (including when the OpenAI client cannot
    even be created, e.g. a missing API key)."""

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
        # Long-term memory (see app.memory): the orchestrator sets this to the
        # recalled past cases for the run in progress, and `run` appends it to
        # the input. Empty means the agent runs without precedent.
        self.memory_context: str = ""
        self.last_trace: Dict[str, Any] = _new_trace(self.model)

    def run(
        self,
        user_message: str,
        response_model: Type[ResponseModel],
        max_tool_iterations: Optional[int] = None,
    ) -> ResponseModel:
        max_tool_iterations = max_tool_iterations or config.MAX_TOOL_ITERATIONS
        trace = self.last_trace = _new_trace(self.model)
        trace["started_at"] = utcnow_iso()
        started = time.perf_counter()
        try:
            return self._run_loop(trace, user_message, response_model, max_tool_iterations)
        except Exception as exc:
            trace["error"] = {"type": type(exc).__name__, "message": str(exc)[:_ERROR_PREVIEW_CHARS]}
            raise
        finally:
            trace["latency_ms"] = _ms_since(started)
            self._log_run(trace)

    def _log_run(self, trace: Dict[str, Any]) -> None:
        failed_tools = sum(1 for call in trace["tool_calls"] if not tool_succeeded(call["result"]))
        error = trace["error"]
        emit(
            logger,
            "agent_run",
            logging.ERROR if error else logging.INFO,
            agent=self.name,
            model=trace["model"],
            status="error" if error else "success",
            latency_ms=trace["latency_ms"],
            iterations=trace["iterations"],
            tool_calls=len(trace["tool_calls"]),
            tool_errors=failed_tools,
            total_tokens=trace["total_tokens"],
            error_type=error["type"] if error else None,
        )

    def _run_loop(
        self,
        trace: Dict[str, Any],
        user_message: str,
        response_model: Type[ResponseModel],
        max_tool_iterations: int,
    ) -> ResponseModel:
        client = get_client()

        if self.memory_context:
            user_message = f"{user_message}\n\n{self.memory_context}"

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

            llm_started = time.perf_counter()
            completion = client.chat.completions.create(**kwargs)
            llm_ms = _ms_since(llm_started)

            usage = getattr(completion, "usage", None)
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            total_tokens = getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens)
            trace["iterations"] += 1
            trace["prompt_tokens"] += prompt_tokens
            trace["completion_tokens"] += completion_tokens
            trace["total_tokens"] += total_tokens

            message = completion.choices[0].message
            tool_calls = getattr(message, "tool_calls", None)
            trace["steps"].append(
                {
                    "n": len(trace["steps"]) + 1,
                    "type": "llm",
                    "latency_ms": llm_ms,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "requested_tools": [tc.function.name for tc in tool_calls] if tool_calls else [],
                }
            )

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
                    tool_started = time.perf_counter()
                    if fn is None:
                        result: Any = {"error": f"Unknown tool '{tc.function.name}'"}
                    else:
                        try:
                            result = fn(**args)
                        except Exception as exc:  # tool failures shouldn't crash the agent
                            result = {"error": str(exc)}
                    tool_ms = _ms_since(tool_started)
                    ok = tool_succeeded(result)
                    trace["tool_calls"].append(
                        {
                            "tool": tc.function.name,
                            "arguments": args,
                            "result": result,
                            "latency_ms": tool_ms,
                        }
                    )
                    trace["steps"].append(
                        {
                            "n": len(trace["steps"]) + 1,
                            "type": "tool",
                            "name": tc.function.name,
                            "latency_ms": tool_ms,
                            "ok": ok,
                        }
                    )
                    # Tool name, timing and outcome only: arguments can hold submitted text.
                    emit(
                        logger,
                        "tool_call",
                        logging.INFO if ok else logging.WARNING,
                        agent=self.name,
                        tool=tc.function.name,
                        ok=ok,
                        latency_ms=tool_ms,
                        error=None if ok else str(result.get("error"))[:200],
                    )
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
