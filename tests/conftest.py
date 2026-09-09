import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("OPENAI_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def bypass_auth():
    """Tests exercise the API directly, not through a real Supabase session."""
    from app.api import app
    from app.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: {
        "id": "test-user",
        "email": "test@example.com",
    }
    yield
    app.dependency_overrides.pop(get_current_user, None)


class FakeSupabaseDB:
    """In-memory stand-in for app.db, so tests don't need a live Supabase
    project. Understands just enough PostgREST query syntax (eq. filters,
    order=col.asc/desc) for the platform's own query patterns."""

    def __init__(self):
        self.tables: Dict[str, List[Dict[str, Any]]] = {}

    def _table(self, name: str) -> List[Dict[str, Any]]:
        return self.tables.setdefault(name, [])

    @staticmethod
    def _matches(row: Dict[str, Any], params: Dict[str, Any]) -> bool:
        for key, value in params.items():
            if key in ("select", "order"):
                continue
            if isinstance(value, str) and value.startswith("eq."):
                if str(row.get(key)) != value[len("eq."):]:
                    return False
        return True

    def select(self, table: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        params = params or {}
        rows = [dict(r) for r in self._table(table) if self._matches(r, params)]
        order = params.get("order")
        if order:
            column, _, direction = order.partition(".")
            rows.sort(key=lambda r: r.get(column) or "", reverse=(direction == "desc"))
        return rows

    def insert(self, table: str, row: Dict[str, Any]) -> Dict[str, Any]:
        row_id = row.get("id")
        if row_id is not None and any(r.get("id") == row_id for r in self._table(table)):
            raise ValueError(f"duplicate key value violates unique constraint (id={row_id!r})")
        stored = dict(row)
        stored.setdefault("created_at", "2024-01-01T00:00:00+00:00")
        self._table(table).append(stored)
        return dict(stored)

    def update(self, table: str, match: Dict[str, Any], values: Dict[str, Any]) -> List[Dict[str, Any]]:
        updated = []
        for row in self._table(table):
            if all(str(row.get(k)) == str(v) for k, v in match.items()):
                row.update(values)
                updated.append(dict(row))
        return updated

    def upsert(self, table: str, row: Dict[str, Any], on_conflict: str) -> Dict[str, Any]:
        key = row.get(on_conflict)
        rows = self._table(table)
        for existing in rows:
            if existing.get(on_conflict) == key:
                existing.update(row)
                return dict(existing)
        stored = dict(row)
        stored.setdefault("created_at", "2024-01-01T00:00:00+00:00")
        rows.append(stored)
        return dict(stored)


@pytest.fixture(autouse=True)
def fake_supabase(monkeypatch):
    """Point app.db at an in-memory store instead of a live Supabase
    project, and seed it with the same policies/risk_rules the platform
    ships in data/*.yaml (the same data scripts/seed_supabase.py loads)."""
    from app import config, db as db_module

    fake = FakeSupabaseDB()
    monkeypatch.setattr(db_module, "select", fake.select)
    monkeypatch.setattr(db_module, "insert", fake.insert)
    monkeypatch.setattr(db_module, "update", fake.update)
    monkeypatch.setattr(db_module, "upsert", fake.upsert)

    from app.tools.policy_repository import add_policy
    from app.tools.risk_rules import add_risk_rule

    policies_data = yaml.safe_load(config.POLICIES_FILE.read_text(encoding="utf-8")) or {}
    for policy in policies_data.get("policies", []):
        add_policy(policy)

    risk_rules_data = yaml.safe_load(config.RISK_RULES_FILE.read_text(encoding="utf-8")) or {}
    for rule in risk_rules_data.get("risk_rules", []):
        add_risk_rule(rule)

    yield fake
