"""Agent observability: request ids, structured logs, trace capture, the
agent_runs record linked to the audit log, metrics, and the health report."""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app import config
from app.agents.base import AgentError, BaseAgent
from app.api import app
from app.llm_client import ConfigurationError
from app.models import AIUseCase
from app.observability import metrics as metrics_module
from app.observability.context import get_request_id, request_context, sanitize_request_id
from app.observability.health import (
    CRITICAL,
    DEGRADED,
    HEALTHY,
    build_health_report,
    classify_run_error,
)
from app.observability.logging_config import JsonFormatter, TextFormatter, configure_logging, emit
from app.observability.metrics import compute_metrics, load_window, percentile
from app.observability.pricing import estimate_cost_usd, price_for
from app.observability.recorder import build_run_row, record_agent_run
from app.orchestrator import GovernanceOrchestrator
from app.tools.audit_log import get_audit_log

client = TestClient(app)
NOW = datetime.now(timezone.utc)


# --- request id ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, accepted",
    [
        ("abcdef123456", True),
        ("req-2024.06_ABC", True),
        ("short", False),  # under 8 chars
        ("x" * 65, False),  # over 64 chars
        ("has space in it", False),
        ("line\nbreak-injected", False),
        ('quote"injection-attempt', False),
        ("", False),
        (None, False),
    ],
)
def test_request_id_sanitization(value, accepted):
    assert sanitize_request_id(value) == (value if accepted else None)


def test_request_context_binds_and_restores():
    assert get_request_id() is None
    with request_context("outer-request-1") as outer:
        assert outer == get_request_id() == "outer-request-1"
        with request_context("bad id") as inner:  # rejected -> fresh generated id
            assert inner != "bad id" and get_request_id() == inner
        assert get_request_id() == "outer-request-1"
    assert get_request_id() is None


# --- structured logging ---------------------------------------------------------------


def _record(**fields):
    logger = logging.getLogger("governai.test")
    record = logger.makeRecord("governai.test", logging.WARNING, __file__, 1, "tool_call", (), None)
    record.fields = fields
    record.request_id = get_request_id()
    return record


def test_json_log_line_has_request_id_and_fields():
    with request_context("req-abc-12345"):
        line = JsonFormatter().format(_record(tool="get_policies", latency_ms=12.5, ok=False))

    data = json.loads(line)
    assert data["event"] == "tool_call" and data["level"] == "WARNING"
    assert data["request_id"] == "req-abc-12345"
    assert (data["tool"], data["latency_ms"], data["ok"]) == ("get_policies", 12.5, False)
    assert data["ts"].endswith("+00:00")


def test_text_log_line_and_null_fields_omitted():
    line = TextFormatter().format(_record(tool="x", error=None))
    assert "tool_call" in line and "tool=x" in line and "error=" not in line


def test_emit_respects_level():
    logger = logging.getLogger("governai.level_test")
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger.addHandler(Capture())
    logger.setLevel(logging.WARNING)
    try:
        emit(logger, "quiet", logging.INFO, a=1)
        emit(logger, "loud", logging.ERROR, a=2)
    finally:
        logger.handlers.clear()
    assert [r.getMessage() for r in records] == ["loud"]
    assert records[0].fields == {"a": 2}


def test_configure_logging_is_idempotent():
    root = logging.getLogger("governai")
    before = (list(root.handlers), root.level, root.propagate)
    try:
        configure_logging(level="DEBUG", fmt="text")
        configure_logging(level="INFO", fmt="json")
        ours = [h for h in root.handlers if getattr(h, "_governai", False)]
        assert len(ours) == 1 and isinstance(ours[0].formatter, JsonFormatter)
        assert root.level == logging.INFO
    finally:
        root.handlers[:] = before[0]
        root.setLevel(before[1])
        root.propagate = before[2]


# --- pricing -----------------------------------------------------------------------------


def test_price_lookup_prefers_the_most_specific_model_name():
    assert price_for("gpt-4o-mini") == (0.15, 0.60)
    assert price_for("gpt-4o-mini-2024-07-18") == (0.15, 0.60)  # not the pricier gpt-4o
    assert price_for("gpt-4o-2024-08-06") == (2.50, 10.00)
    assert price_for("some-future-model") == price_for("gpt-4o-mini")
    assert price_for(None) == price_for("gpt-4o-mini")


def test_cost_estimate():
    # 1M prompt + 1M completion tokens at gpt-4o-mini = 0.15 + 0.60
    assert estimate_cost_usd("gpt-4o-mini", 1_000_000, 1_000_000) == 0.75
    assert estimate_cost_usd("gpt-4o-mini", 0, 0) == 0
    # No prompt/completion split from the provider: price the total as input (lower bound).
    assert estimate_cost_usd("gpt-4o-mini", 0, 0, total_tokens=1_000_000) == 0.15


def test_price_override_and_malformed_override(monkeypatch):
    monkeypatch.setattr(config, "MODEL_PRICES_JSON", '{"gpt-4o-mini": [1.0, 2.0], "custom": [3, 4]}')
    assert price_for("gpt-4o-mini") == (1.0, 2.0) and price_for("custom-v2") == (3.0, 4.0)

    monkeypatch.setattr(config, "MODEL_PRICES_JSON", "{not json")
    assert price_for("gpt-4o-mini") == (0.15, 0.60), "a bad override must not break runs"


# --- BaseAgent trace capture -------------------------------------------------------------


class Answer(BaseModel):
    ok: bool


def _completion(content="", tool_calls=None, prompt=100, completion=20):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def _call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


class Scripted:
    def __init__(self, responses):
        self._responses = list(responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **_kwargs):
        return self._responses.pop(0)


def _agent(**tool_functions):
    return BaseAgent(system_prompt="agent", tools=[], tool_functions=tool_functions, model="gpt-4o-mini")


def test_trace_records_steps_latency_tokens_and_tool_outcomes(monkeypatch):
    def slow_lookup(x: int):
        time.sleep(0.02)
        return {"x": x}

    def broken(**_kwargs):
        raise RuntimeError("database down")

    client_ = Scripted([
        _completion(tool_calls=[_call("1", "lookup", {"x": 1}), _call("2", "broken", {}), _call("3", "ghost", {})]),
        _completion(content=json.dumps({"ok": True}), prompt=200, completion=10),
    ])
    monkeypatch.setattr("app.agents.base.get_client", lambda: client_)
    agent = _agent(lookup=slow_lookup, broken=broken)

    agent.run("go", Answer)
    trace = agent.last_trace

    assert trace["model"] == "gpt-4o-mini" and trace["started_at"] and trace["error"] is None
    assert trace["iterations"] == 2
    assert (trace["prompt_tokens"], trace["completion_tokens"], trace["total_tokens"]) == (300, 30, 330)
    assert [s["type"] for s in trace["steps"]] == ["llm", "tool", "tool", "tool", "llm"]
    assert [s["n"] for s in trace["steps"]] == [1, 2, 3, 4, 5]
    assert trace["steps"][0]["requested_tools"] == ["lookup", "broken", "ghost"]
    assert [s["ok"] for s in trace["steps"] if s["type"] == "tool"] == [True, False, False]
    assert trace["tool_calls"][0]["latency_ms"] >= 15
    assert "database down" in trace["tool_calls"][1]["result"]["error"]
    assert "Unknown tool" in trace["tool_calls"][2]["result"]["error"]
    assert trace["latency_ms"] >= trace["tool_calls"][0]["latency_ms"] > 0


def test_trace_survives_a_failing_run(monkeypatch):
    monkeypatch.setattr("app.agents.base.get_client", lambda: Scripted([_completion(content="not json")]))
    agent = _agent()

    with pytest.raises(AgentError):
        agent.run("go", Answer)

    assert agent.last_trace["error"]["type"] == "AgentError"
    assert agent.last_trace["started_at"] and agent.last_trace["latency_ms"] >= 0
    assert agent.last_trace["iterations"] == 1


def test_a_long_unparseable_reply_is_only_previewed_in_the_persisted_error(monkeypatch):
    monkeypatch.setattr("app.agents.base.get_client", lambda: Scripted([_completion(content="secret " * 500)]))
    agent = _agent()

    with pytest.raises(AgentError):
        agent.run("go", Answer)

    message = agent.last_trace["error"]["message"]
    assert message.count("secret") <= 20 and len(message) < 250


def test_a_missing_api_key_is_traced_not_lost(monkeypatch):
    def no_key():
        raise ConfigurationError("OPENAI_API_KEY is not set.")

    monkeypatch.setattr("app.agents.base.get_client", no_key)
    agent = _agent()

    with pytest.raises(ConfigurationError):
        agent.run("go", Answer)

    assert agent.last_trace["error"]["type"] == "ConfigurationError"
    assert agent.last_trace["iterations"] == 0


def test_trace_resets_between_runs(monkeypatch):
    monkeypatch.setattr(
        "app.agents.base.get_client",
        lambda: Scripted([_completion(content=json.dumps({"ok": True})), _completion(content=json.dumps({"ok": True}))]),
    )
    agent = _agent()
    agent.run("a", Answer)
    first = agent.last_trace
    agent.run("b", Answer)
    assert agent.last_trace is not first and agent.last_trace["iterations"] == 1


def test_tool_log_lines_never_contain_arguments_or_documents(monkeypatch, caplog):
    secret = "CONFIDENTIAL-DOC-TEXT-9931"
    monkeypatch.setattr(
        "app.agents.base.get_client",
        lambda: Scripted([
            _completion(tool_calls=[_call("1", "lookup", {"text": secret})]),
            _completion(content=json.dumps({"ok": True})),
        ]),
    )
    agent = _agent(lookup=lambda text: {"echo": text})

    with caplog.at_level(logging.DEBUG, logger="governai"):
        agent.run("go", Answer)

    assert caplog.records, "expected structured log records"
    assert secret not in "\n".join(JsonFormatter().format(r) for r in caplog.records)


# --- recorder --------------------------------------------------------------------------------


def _trace(**overrides):
    trace = {
        "model": "gpt-4o-mini", "started_at": NOW.isoformat(), "latency_ms": 1200.5, "iterations": 2,
        "prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200,
        "steps": [{"n": 1, "type": "llm", "latency_ms": 900.0}],
        "tool_calls": [
            {"tool": "get_policies", "arguments": {"q": "x" * 500}, "result": [{"id": "POL-001", "body": "y" * 500}],
             "latency_ms": 30.0},
            {"tool": "search_policies", "arguments": {}, "result": {"error": "TIMEOUT"}, "latency_ms": 5000.0},
        ],
        "error": None,
    }
    trace.update(overrides)
    return trace


def test_run_row_contents_and_privacy():
    row = build_run_row(_trace(), agent="policy_compliance_agent", stage="policy_compliance",
                        use_case_id="uc-1", audit_log_id="audit-1", request_id="req-12345678")

    assert (row["agent"], row["stage"], row["status"]) == ("policy_compliance_agent", "policy_compliance", "success")
    assert (row["use_case_id"], row["audit_log_id"], row["request_id"]) == ("uc-1", "audit-1", "req-12345678")
    assert (row["tool_call_count"], row["tool_error_count"], row["total_tokens"]) == (2, 1, 1200)
    assert row["estimated_cost_usd"] == pytest.approx(1000 * 0.15 / 1e6 + 200 * 0.60 / 1e6, abs=1e-6)
    assert row["error_type"] is None and row["steps"]
    # Full arguments/outputs are never persisted: only short previews.
    assert all(len(call["arguments_preview"]) <= 200 and len(call["output_preview"]) <= 200 for call in row["tool_calls"])
    assert "result" not in row["tool_calls"][0] and "arguments" not in row["tool_calls"][0]


def test_failed_run_row():
    row = build_run_row(_trace(error={"type": "AgentError", "message": "bad json"}), agent="a", stage="s", use_case_id="u")
    assert (row["status"], row["error_type"], row["error_message"]) == ("error", "AgentError", "bad json")


def test_request_id_defaults_to_the_current_context():
    with request_context("ctx-request-1"):
        assert build_run_row(_trace(), agent="a", stage="s", use_case_id="u")["request_id"] == "ctx-request-1"


def test_agent_that_never_ran_is_not_recorded(fake_supabase):
    agent = _agent()
    assert record_agent_run(agent, stage="s", use_case_id="u") is None
    assert "agent_runs" not in fake_supabase.tables


def test_persist_failure_is_swallowed_and_logged(monkeypatch, caplog):
    def boom(table, row):
        raise RuntimeError("relation agent_runs does not exist")

    monkeypatch.setattr("app.db.insert", boom)
    agent = _agent()
    agent.last_trace = _trace()

    with caplog.at_level(logging.WARNING, logger="governai.observability"):
        assert record_agent_run(agent, stage="s", use_case_id="u") is None

    assert any(r.getMessage() == "agent_run_persist_failed" for r in caplog.records)


# --- orchestrator: runs linked to the audit log --------------------------------------------------


class ThreeAgentModel:
    """Scripted model for all four agents; the Policy agent uses one tool."""

    def __init__(self, risk_reply=None):
        self.risk_reply = risk_reply
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, **_kwargs):
        system = messages[0]["content"]
        if "You are the Risk Assessment Agent" in system:
            reply = self.risk_reply or json.dumps({"risk_level": "low", "risk_score": 10, "rationale": "low"})
            return _completion(reply)
        if "You are the Policy Compliance Agent" in system:
            if any(m.get("role") == "tool" for m in messages):
                return _completion(json.dumps({"status": "compliant", "rationale": "ok"}))
            return _completion(tool_calls=[_call("c1", "get_policies", {})])
        if "You are the Review Agent" in system:
            return _completion(json.dumps({"verdict": "approved", "rationale": "ok"}))
        return _completion(json.dumps({"decision": "approve", "conditions": [], "rationale": "ok"}))


USE_CASE = dict(name="Notes", description="Summarizes notes.", owner="team", documentation="Docs.")


def _rows(fake, table):
    return list(fake.tables.get(table, []))


def test_each_agent_run_is_recorded_and_linked_to_its_audit_entry(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.agents.base.get_client", lambda: ThreeAgentModel())
    use_case = AIUseCase(**USE_CASE)

    with request_context("pipeline-req-01"):
        GovernanceOrchestrator().run(use_case)

    runs = _rows(fake_supabase, "agent_runs")
    assert [r["stage"] for r in runs] == ["risk_assessment", "policy_compliance", "decision", "review"]
    audit = {e["id"]: e for e in get_audit_log(use_case.id)}
    for run in runs:
        assert run["use_case_id"] == use_case.id and run["request_id"] == "pipeline-req-01"
        assert run["status"] == "success" and run["total_tokens"] == 120 * run["iterations"]
        linked = audit[run["audit_log_id"]]
        assert linked["stage"] == run["stage"] and linked["actor"] == run["agent"]
    policy = next(r for r in runs if r["stage"] == "policy_compliance")
    assert policy["tool_call_count"] == 1 and policy["iterations"] == 2 and policy["tool_calls"][0]["tool"] == "get_policies"


def test_agent_failure_writes_a_failed_audit_entry_and_a_linked_error_run(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.agents.base.get_client", lambda: ThreeAgentModel(risk_reply="not json at all"))
    use_case = AIUseCase(**USE_CASE)

    with pytest.raises(AgentError):
        GovernanceOrchestrator().run(use_case)

    stages = [e["stage"] for e in get_audit_log(use_case.id)]
    assert stages == ["intake", "risk_assessment_failed"], "no later stage runs after a failure"
    (run,) = _rows(fake_supabase, "agent_runs")
    failed_entry = get_audit_log(use_case.id)[-1]
    assert (run["status"], run["error_type"], run["audit_log_id"]) == ("error", "AgentError", failed_entry["id"])
    assert failed_entry["data"]["error_type"] == "AgentError"


def test_a_missing_api_key_produces_a_critical_error_run(monkeypatch, fake_supabase):
    def no_key():
        raise ConfigurationError("OPENAI_API_KEY is not set.")

    monkeypatch.setattr("app.agents.base.get_client", no_key)
    with pytest.raises(ConfigurationError):
        GovernanceOrchestrator().run(AIUseCase(**USE_CASE))

    (run,) = _rows(fake_supabase, "agent_runs")
    assert run["error_type"] == "ConfigurationError"
    assert classify_run_error(run["error_type"], run["error_message"]) == "CONFIGURATION"


def test_a_recording_outage_does_not_break_the_pipeline(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.agents.base.get_client", lambda: ThreeAgentModel())
    real_insert = fake_supabase.insert

    def insert(table, row):
        if table == "agent_runs":
            raise RuntimeError("agent_runs unavailable")
        return real_insert(table, row)

    monkeypatch.setattr("app.db.insert", insert)

    report = GovernanceOrchestrator().run(AIUseCase(**USE_CASE))

    assert report.decision.decision.value == "approve"
    assert "agent_runs" not in fake_supabase.tables


# --- metrics ------------------------------------------------------------------------------------


def make_run(**overrides):
    row = {
        "id": "r", "use_case_id": "uc", "agent": "risk_assessment_agent", "stage": "risk_assessment",
        "model": "gpt-4o-mini", "status": "success", "error_type": None, "error_message": None,
        "started_at": NOW.isoformat(), "latency_ms": 1000.0, "iterations": 1, "tool_call_count": 0,
        "tool_error_count": 0, "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
        "estimated_cost_usd": 0.001, "steps": [], "tool_calls": [],
    }
    row.update(overrides)
    return row


def tool(name="get_policies", ok=True, ms=50.0, out='{"id": "POL-001"}'):
    return {"tool": name, "ok": ok, "latency_ms": ms, "arguments_preview": "{}", "output_preview": out}


WINDOW = {"hours": 24, "since": (NOW - timedelta(hours=24)).isoformat(), "until": NOW.isoformat()}


def test_percentile_is_nearest_rank():
    values = list(range(1, 21))  # 1..20
    assert percentile(values, 50) == 10
    assert percentile(values, 95) == 19
    assert percentile(values, 100) == 20
    assert percentile([7], 95) == 7
    assert percentile([], 95) == 0.0


def test_compute_metrics_headline_numbers():
    runs = [make_run(id=str(i), latency_ms=float(i * 100)) for i in range(1, 11)]  # 100..1000
    runs[3]["status"] = "error"
    metrics = compute_metrics(runs, [], WINDOW)

    assert metrics["runs"] == {"total": 10, "success": 9, "error": 1, "success_rate": 0.9}
    assert metrics["latency"] == {"avg_ms": 550.0, "p50_ms": 500.0, "p95_ms": 1000.0, "max_ms": 1000.0}
    assert metrics["tokens"] == {"prompt": 1000, "completion": 200, "total": 1200}
    assert metrics["cost"]["total_usd"] == 0.01 and metrics["cost"]["avg_per_run_usd"] == 0.001
    assert metrics["cost"]["by_model"] == {"gpt-4o-mini": 0.01}


def test_compute_metrics_per_tool_and_per_agent():
    runs = [
        make_run(id="1", agent="policy_compliance_agent", tool_call_count=2,
                 tool_calls=[tool("get_policies", ms=100.0), tool("search_policies", ok=False, ms=500.0)]),
        make_run(id="2", agent="policy_compliance_agent", tool_call_count=1, tool_calls=[tool("get_policies", ms=300.0)]),
        make_run(id="3", agent="decision_agent", status="error", tool_calls=[]),
    ]
    metrics = compute_metrics(runs, [], WINDOW)

    by_tool = {t["tool"]: t for t in metrics["tools"]}
    assert by_tool["get_policies"] == {"tool": "get_policies", "calls": 2, "errors": 0, "error_rate": 0.0,
                                       "avg_latency_ms": 200.0, "p95_latency_ms": 300.0}
    assert (by_tool["search_policies"]["calls"], by_tool["search_policies"]["error_rate"]) == (1, 1.0)
    assert metrics["tools"][0]["tool"] == "get_policies", "tools sorted by usage"

    by_agent = {a["agent"]: a for a in metrics["agents"]}
    assert by_agent["policy_compliance_agent"]["runs"] == 2 and by_agent["policy_compliance_agent"]["avg_tool_calls"] == 1.5
    assert by_agent["decision_agent"]["success_rate"] == 0.0


def test_decision_mix():
    reports = [{"decision": "approve"}, {"decision": "block"}, {"decision": "block"}, {"decision": "require_human_approval"}]
    decisions = compute_metrics([], reports, WINDOW)["decisions"]

    assert decisions["total"] == 4
    assert decisions["counts"] == {"approve": 1, "require_human_approval": 1, "block": 2}
    assert decisions["shares"]["block"] == 0.5


def test_empty_window_is_all_zeros_not_an_error():
    metrics = compute_metrics([], [], WINDOW)
    assert metrics["runs"]["success_rate"] == 0.0 and metrics["latency"]["p95_ms"] == 0.0
    assert metrics["tools"] == [] and metrics["agents"] == [] and metrics["decisions"]["total"] == 0


def test_load_window_filters_by_time_and_flags_truncation(fake_supabase, monkeypatch):
    fake_supabase.insert("agent_runs", make_run(id="new", started_at=(NOW - timedelta(hours=1)).isoformat()))
    fake_supabase.insert("agent_runs", make_run(id="old", started_at=(NOW - timedelta(hours=48)).isoformat()))
    fake_supabase.insert("governance_reports", {"use_case_id": "u", "decision": "approve", "created_at": NOW.isoformat()})
    fake_supabase.insert("governance_reports", {"use_case_id": "v", "decision": "block",
                                                "created_at": (NOW - timedelta(days=9)).isoformat()})

    data = load_window(24, NOW)

    assert [r["id"] for r in data["runs"]] == ["new"]
    assert [r["decision"] for r in data["reports"]] == ["approve"]
    assert data["truncated"] is False

    monkeypatch.setattr(metrics_module, "MAX_ROWS", 1)
    assert load_window(24, NOW)["truncated"] is True


# --- health report --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error_type, message, expected",
    [
        ("ConfigurationError", "OPENAI_API_KEY is not set.", "CONFIGURATION"),
        ("AuthenticationError", "Incorrect API key provided", "CONFIGURATION"),
        ("SupabaseNotConfiguredError", "", "CONFIGURATION"),
        ("AgentError", "risk agent exceeded max_tool_iterations (10) without a final answer", "LOOP_LIMIT"),
        ("RateLimitError", "Rate limit reached", "PROVIDER_UNAVAILABLE"),
        ("APITimeoutError", "Request timed out.", "PROVIDER_UNAVAILABLE"),
        ("InternalServerError", "503 Service Unavailable", "PROVIDER_UNAVAILABLE"),
        ("AgentError", "Could not parse a JSON object out of model output: 'hi'", "OUTPUT_CONTRACT"),
        ("AgentError", "decision_agent produced output that does not match DecisionResult", "OUTPUT_CONTRACT"),
        ("KeyError", "'decision'", "UNKNOWN"),
        (None, None, "UNKNOWN"),
    ],
)
def test_failure_classification(error_type, message, expected):
    assert classify_run_error(error_type, message) == expected


def _ok_runs(n, **overrides):
    return [make_run(id=f"ok{i}", **overrides) for i in range(n)]


def _failed_runs(n, error_type="AgentError", message="Could not parse a JSON object", **overrides):
    return [make_run(id=f"bad{i}", status="error", error_type=error_type, error_message=message, **overrides) for i in range(n)]


def test_healthy_when_everything_succeeds():
    report = build_health_report(_ok_runs(10), WINDOW)

    assert report["status"] == HEALTHY and report["reasons"] == []
    assert report["recommended_actions"] == [] and report["data_sufficient"] is True
    assert report["summary"]["success_rate"] == 1.0


@pytest.mark.parametrize(
    "ok, failed, expected",
    [(10, 0, HEALTHY), (9, 1, HEALTHY), (8, 2, DEGRADED), (7, 3, DEGRADED), (6, 4, CRITICAL), (0, 10, CRITICAL)],
)
def test_status_follows_the_success_rate_thresholds(ok, failed, expected):
    # 90% is healthy, under 90% degraded, under 70% critical.
    report = build_health_report(_ok_runs(ok) + _failed_runs(failed), WINDOW)
    assert report["status"] == expected


def test_a_handful_of_runs_cannot_flip_the_status_on_rate_alone():
    report = build_health_report(_ok_runs(2) + _failed_runs(2), WINDOW)

    assert report["status"] == HEALTHY and report["data_sufficient"] is False
    assert any("at least 5" in note for note in report["notes"])


def test_a_configuration_failure_is_critical_even_with_few_runs():
    report = build_health_report(_failed_runs(1, "ConfigurationError", "OPENAI_API_KEY is not set."), WINDOW)

    assert report["status"] == CRITICAL
    assert report["recommended_actions"][0]["category"] == "CONFIGURATION"


def test_repeated_provider_failures_degrade_the_status():
    report = build_health_report(_ok_runs(18) + _failed_runs(2, "RateLimitError", "Rate limit reached"), WINDOW)

    assert report["status"] == DEGRADED
    assert any("PROVIDER_UNAVAILABLE" in reason for reason in report["reasons"])


def test_a_noisy_tool_degrades_the_status_even_when_runs_succeed():
    runs = [make_run(id=str(i), tool_call_count=1, tool_calls=[tool("get_policies", ok=(i % 5 != 0), out='{"error": "TIMEOUT"}')])
            for i in range(20)]  # 4 of 20 calls fail = 20%
    report = build_health_report(runs, WINDOW)

    assert report["summary"]["success_rate"] == 1.0
    assert report["status"] == DEGRADED
    assert any("get_policies" in reason for reason in report["reasons"])
    tool_failure = next(c for c in report["failure_categories"] if c["category"] == "TOOL_FAILURE")
    assert tool_failure["runs_affected"] == 4 and tool_failure["tool_calls_affected"] == 4
    assert tool_failure["top_errors"][0]["error"] == "get_policies: TIMEOUT"


def test_failure_categories_keep_a_fixed_severity_order_and_flag_the_dominant_one():
    runs = _ok_runs(10) + _failed_runs(3) + _failed_runs(1, "RateLimitError", "rate limit")
    categories = build_health_report(runs, WINDOW)["failure_categories"]

    assert [c["category"] for c in categories] == [
        "CONFIGURATION", "PROVIDER_UNAVAILABLE", "TOOL_FAILURE", "OUTPUT_CONTRACT", "LOOP_LIMIT", "UNKNOWN"
    ]
    assert [c["category"] for c in categories if c["dominant"]] == ["OUTPUT_CONTRACT"]
    assert {c["category"]: c["runs_affected"] for c in categories}["PROVIDER_UNAVAILABLE"] == 1


def test_recommended_actions_are_data_driven_and_ordered_by_severity():
    runs = _ok_runs(10) + _failed_runs(1, "RateLimitError", "rate limit") + _failed_runs(2)
    actions = build_health_report(runs, WINDOW)["recommended_actions"]

    assert [a["category"] for a in actions] == ["PROVIDER_UNAVAILABLE", "OUTPUT_CONTRACT"]
    assert [a["priority"] for a in actions] == [1, 2]
    assert all(a["action"] and a["reason"] for a in actions)


def test_latency_anomaly_uses_mean_plus_two_std():
    # Nine runs at 100 ms and one at 1000 ms: mean 190, sample std ~284.6, threshold ~759.
    runs = [make_run(id=f"f{i}", latency_ms=100.0) for i in range(9)] + [make_run(id="slow", latency_ms=1000.0)]
    anomalies = build_health_report(runs, WINDOW)["latency_anomalies"]

    (group,) = [g for g in anomalies["groups"] if g["kind"] == "agent_run"]
    assert group["mean_ms"] == 190.0 and group["samples"] == 10
    assert group["threshold_ms"] == pytest.approx(190 + 2 * 284.6, abs=1.0)
    assert [item["run_id"] for item in anomalies["top"]] == ["slow"]
    assert anomalies["total"] == 1 and group["anomaly_rate"] == 0.1


def test_tool_call_latency_anomalies_are_flagged_per_tool():
    calls = [tool("get_policies", ms=50.0) for _ in range(9)] + [tool("get_policies", ms=900.0)]
    runs = [make_run(id=f"r{i}", latency_ms=100.0, tool_calls=[calls[i]]) for i in range(10)]

    anomalies = build_health_report(runs, WINDOW)["latency_anomalies"]

    flagged = [item for item in anomalies["top"] if item["kind"] == "tool_call"]
    assert [(item["name"], item["run_id"]) for item in flagged] == [("get_policies", "r9")]
    assert any("get_policies" in a["action"] for a in build_health_report(runs, WINDOW)["recommended_actions"])


def test_failed_calls_do_not_pollute_the_latency_baseline():
    runs = [make_run(id=f"f{i}", latency_ms=100.0) for i in range(9)]
    runs += [make_run(id="failed-slow", status="error", error_type="APITimeoutError", latency_ms=60000.0)]

    group = next(g for g in build_health_report(runs, WINDOW)["latency_anomalies"]["groups"] if g["kind"] == "agent_run")
    assert group["mean_ms"] == 100.0 and group["anomalies"] == 0


def test_too_few_samples_means_no_baseline_and_a_note():
    report = build_health_report(_ok_runs(4), WINDOW)

    assert report["latency_anomalies"]["groups"] == [] and report["latency_anomalies"]["total"] == 0
    assert any("baseline" in note for note in report["notes"])


def test_worst_component_and_no_data_case():
    runs = _ok_runs(6) + _failed_runs(3, "RateLimitError", "rate limit", agent="decision_agent")
    runs += [make_run(id=f"d{i}", agent="decision_agent") for i in range(3)]
    worst = build_health_report(runs, WINDOW)["worst_component"]

    assert (worst["kind"], worst["name"], worst["failed"], worst["total"]) == ("agent", "decision_agent", 3, 6)
    assert worst["top_error"] == "RateLimitError"

    empty = build_health_report([], WINDOW)
    assert empty["status"] == HEALTHY and empty["worst_component"] is None
    assert any("No agent runs" in note for note in empty["notes"])


def test_truncation_is_reported():
    report = build_health_report(_ok_runs(6), WINDOW, truncated=True)
    assert any("row limit" in note for note in report["notes"])


# --- API -------------------------------------------------------------------------------------------


def _seed_api(fake):
    for i in range(6):
        fake.insert("agent_runs", make_run(id=f"a{i}", started_at=(NOW - timedelta(minutes=i)).isoformat(),
                                           tool_call_count=1, tool_calls=[tool()]))
    fake.insert("governance_reports", {"use_case_id": "u", "decision": "approve", "created_at": NOW.isoformat()})


def test_metrics_endpoint(fake_supabase):
    _seed_api(fake_supabase)

    response = client.get("/metrics", params={"hours": 24})

    assert response.status_code == 200
    body = response.json()
    assert body["runs"]["total"] == 6 and body["runs"]["success_rate"] == 1.0
    assert body["tools"][0]["tool"] == "get_policies" and body["decisions"]["counts"]["approve"] == 1
    assert {"avg_ms", "p95_ms"} <= set(body["latency"])


def test_health_report_endpoint(fake_supabase):
    _seed_api(fake_supabase)

    response = client.get("/metrics/health-report")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == HEALTHY and body["summary"]["runs"] == 6
    assert {"failure_categories", "latency_anomalies", "recommended_actions", "reasons"} <= set(body)


@pytest.mark.parametrize("path", ["/metrics", "/metrics/health-report"])
@pytest.mark.parametrize("hours", [0, -1, 721, "abc"])
def test_hours_is_validated(path, hours):
    assert client.get(path, params={"hours": hours}).status_code == 422


def test_observability_endpoints_require_authentication():
    from app.auth import get_current_user

    app.dependency_overrides.pop(get_current_user, None)  # undo the autouse bypass
    for path in ("/metrics", "/metrics/health-report"):
        assert client.get(path).status_code in (401, 503)


def test_request_id_header_is_generated_echoed_and_sanitized():
    generated = client.get("/health").headers["X-Request-ID"]
    assert len(generated) == 32

    assert client.get("/health", headers={"X-Request-ID": "caller-supplied-123"}).headers["X-Request-ID"] == "caller-supplied-123"

    replaced = client.get("/health", headers={"X-Request-ID": "bad id\twith junk"}).headers["X-Request-ID"]
    assert replaced != "bad id\twith junk" and len(replaced) == 32


def test_request_id_flows_from_the_http_request_into_the_agent_runs(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.agents.base.get_client", lambda: ThreeAgentModel())

    response = client.post(
        "/use-cases", json=USE_CASE, headers={"X-Request-ID": "trace-me-end-to-end"}
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "trace-me-end-to-end"
    runs = _rows(fake_supabase, "agent_runs")
    assert len(runs) == 4 and {r["request_id"] for r in runs} == {"trace-me-end-to-end"}
