"""Tests for the read-path optimizations that keep page loads fast: the shared
Supabase clients, the auth token cache, slim list queries, the bounded audit
log and the metrics window cache."""
import asyncio

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import auth, db as db_module
from app.api import app
from app.models import AIUseCase, PipelineRun, PipelineRunStatus
from app.observability import metrics as metrics_module
from app.observability.metrics import metrics_for_window
from app.pipeline_runs import RUN_LIST_COLUMNS, list_runs, save_run
from app.reports import USE_CASE_LIST_COLUMNS, list_reports, save_use_case
from app.tools.audit_log import get_audit_log

# conftest replaces app.db's functions with an in-memory fake for every test;
# keep the real ones to test the HTTP layer itself.
_real_select, _real_insert = db_module.select, db_module.insert
_real_update, _real_upsert = db_module.update, db_module.upsert

client = TestClient(app)


# --- Supabase auth token cache ---------------------------------------------------------------------


class _FakeAuthClient:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.calls = 0

    async def get(self, url, headers):
        self.calls += 1
        return httpx.Response(self.status_code, json={"id": "u-1", "email": "a@b.c"})


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setattr(auth, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(auth, "SUPABASE_ANON_KEY", "anon")
    monkeypatch.setattr(auth, "_token_cache", {})
    fake = _FakeAuthClient()
    monkeypatch.setattr(auth, "_client", fake)
    return fake


def _whoami(token):
    return asyncio.run(auth.get_current_user(f"Bearer {token}"))


def test_repeat_requests_with_one_token_reach_supabase_once(auth_env):
    assert _whoami("tok")["id"] == "u-1"
    assert _whoami("tok")["id"] == "u-1"
    assert auth_env.calls == 1

    _whoami("another-token")
    assert auth_env.calls == 2


def test_cached_token_expires(auth_env, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(auth, "_now", lambda: now[0])

    _whoami("tok")
    now[0] += auth.TOKEN_CACHE_TTL_SECONDS - 1
    _whoami("tok")
    assert auth_env.calls == 1

    now[0] += 2
    _whoami("tok")
    assert auth_env.calls == 2


def test_rejected_token_is_not_cached(auth_env):
    auth_env.status_code = 401

    for _ in range(2):
        with pytest.raises(HTTPException) as exc:
            _whoami("bad")
        assert exc.value.status_code == 401
    assert auth_env.calls == 2


def test_token_cache_is_bounded(auth_env, monkeypatch):
    monkeypatch.setattr(auth, "TOKEN_CACHE_MAX_ENTRIES", 3)

    for n in range(10):
        _whoami(f"tok-{n}")

    assert len(auth._token_cache) <= 3


def test_raw_token_is_not_used_as_cache_key(auth_env):
    _whoami("super-secret-token")

    assert all("super-secret-token" not in key for key in auth._token_cache)


# --- pooled Supabase client ------------------------------------------------------------------------


def test_db_calls_go_through_the_shared_client(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params)))
        return httpx.Response(200, json=[{"id": "1"}])

    monkeypatch.setattr(db_module, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(db_module.config, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(db_module.config, "SUPABASE_SERVICE_ROLE_KEY", "service")

    assert _real_select("t", {"select": "id", "order": "id.asc"}) == [{"id": "1"}]
    assert _real_insert("t", {"id": "1"}) == {"id": "1"}
    assert _real_update("t", {"id": "1"}, {"x": 1}) == [{"id": "1"}]
    assert _real_upsert("t", {"id": "1"}, on_conflict="id") == {"id": "1"}

    assert [(method, path) for method, path, _ in seen] == [
        ("GET", "/rest/v1/t"),
        ("POST", "/rest/v1/t"),
        ("PATCH", "/rest/v1/t"),
        ("POST", "/rest/v1/t"),
    ]
    assert seen[0][2] == {"select": "id", "order": "id.asc"}
    assert seen[2][2] == {"id": "eq.1"}
    assert seen[3][2] == {"on_conflict": "id"}


# --- list queries stay slim ------------------------------------------------------------------------


@pytest.fixture
def recorded_selects(monkeypatch):
    """Wrap the (fake) db.select to record each call's table and params."""
    calls = []
    inner = db_module.select

    def spy(table, params=None):
        calls.append((table, dict(params or {})))
        return inner(table, params)

    monkeypatch.setattr(db_module, "select", spy)
    return calls


def test_list_queries_do_not_fetch_documentation_or_memory_context(recorded_selects):
    use_case = AIUseCase(name="Chatbot", description="d", owner="o", documentation="x" * 10_000)
    save_use_case(use_case)
    save_run(
        PipelineRun(
            use_case=use_case,
            status=PipelineRunStatus.AWAITING_STEP_APPROVAL,
            memory_context="recalled cases",
        )
    )

    list_reports()
    list_runs()

    columns = {}
    for table, params in recorded_selects:
        if "select" in params:
            columns.setdefault(table, []).append(params["select"])

    assert "documentation" not in USE_CASE_LIST_COLUMNS.split(",")
    assert "memory_context" not in RUN_LIST_COLUMNS.split(",")
    assert columns["use_cases"] == [USE_CASE_LIST_COLUMNS, USE_CASE_LIST_COLUMNS]
    assert columns["pipeline_runs"] == [RUN_LIST_COLUMNS]


def test_list_columns_are_valid_model_fields():
    """Every column the list asks for must exist on the models it feeds, so a
    typo here can't silently drop data from the list views."""
    use_case_fields = set(AIUseCase.model_fields)
    assert set(USE_CASE_LIST_COLUMNS.split(",")) <= use_case_fields
    assert set(RUN_LIST_COLUMNS.split(",")) <= set(PipelineRun.model_fields) | {"use_case_id"}


# --- audit log is bounded when unfiltered ----------------------------------------------------------


def _seed_audit(fake, count, use_case_id="uc"):
    for n in range(count):
        fake.insert(
            "audit_log",
            {
                "id": f"e{n}",
                "use_case_id": use_case_id,
                "stage": "intake",
                "actor": "system",
                "data": {},
                "created_at": f"2025-01-01T00:00:{n:02d}+00:00",
            },
        )


def test_get_audit_log_limit_keeps_newest_entries_in_chronological_order(fake_supabase):
    _seed_audit(fake_supabase, 5)

    assert [e["id"] for e in get_audit_log(limit=2)] == ["e3", "e4"]
    assert [e["id"] for e in get_audit_log()] == ["e0", "e1", "e2", "e3", "e4"]


def test_audit_log_route_limits_the_unfiltered_list(fake_supabase):
    _seed_audit(fake_supabase, 5)

    everything = client.get("/audit-log").json()
    assert [e["id"] for e in everything] == ["e0", "e1", "e2", "e3", "e4"]  # under the default cap

    limited = client.get("/audit-log", params={"limit": 2}).json()
    assert [e["id"] for e in limited] == ["e3", "e4"]


def test_audit_log_route_never_truncates_one_use_cases_trail(fake_supabase):
    _seed_audit(fake_supabase, 5, use_case_id="uc")
    fake_supabase.insert(  # another use case's entry must not appear in uc's trail
        "audit_log",
        {"id": "x", "use_case_id": "other", "stage": "intake", "actor": "s", "data": {},
         "created_at": "2025-02-01T00:00:00+00:00"},
    )

    trail = client.get("/audit-log", params={"use_case_id": "uc", "limit": 1}).json()

    assert [e["id"] for e in trail] == ["e0", "e1", "e2", "e3", "e4"]


def test_audit_log_route_rejects_a_nonsense_limit():
    assert client.get("/audit-log", params={"limit": 0}).status_code == 422


# --- metrics window cache --------------------------------------------------------------------------


@pytest.fixture
def counted_selects(monkeypatch):
    calls = []
    inner = db_module.select

    def spy(table, params=None):
        calls.append(table)
        return inner(table, params)

    monkeypatch.setattr(db_module, "select", spy)
    return calls


def test_metrics_and_health_share_one_window_read(counted_selects):
    client.get("/metrics", params={"hours": 24})
    client.get("/metrics/health-report", params={"hours": 24})

    # one read of agent_runs and one of governance_reports, not one pair per endpoint
    assert counted_selects.count("agent_runs") == 1
    assert counted_selects.count("governance_reports") == 1


def test_metrics_window_cache_is_per_window_and_expires(counted_selects, monkeypatch):
    now = [500.0]
    monkeypatch.setattr(metrics_module, "_now", lambda: now[0])

    metrics_for_window(24)
    metrics_for_window(24)
    assert counted_selects.count("agent_runs") == 1

    metrics_for_window(168)  # a different window is a different read
    assert counted_selects.count("agent_runs") == 2

    now[0] += metrics_module.WINDOW_CACHE_SECONDS + 1
    metrics_for_window(24)
    assert counted_selects.count("agent_runs") == 3


def test_an_explicit_now_always_reads_fresh(counted_selects):
    from datetime import datetime, timezone

    moment = datetime(2025, 1, 1, tzinfo=timezone.utc)
    metrics_module.load_window(24, moment)
    metrics_module.load_window(24, moment)

    assert counted_selects.count("agent_runs") == 2
