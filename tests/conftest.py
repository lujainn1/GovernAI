import os
import sys
from pathlib import Path

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


@pytest.fixture(autouse=True)
def fake_supabase(monkeypatch):
    """Point app.db at an in-memory store instead of a live Supabase
    project, and seed it with the same policies/risk_rules the platform
    ships in data/*.yaml (the same data scripts/seed_supabase.py loads)."""
    from app import config, db as db_module
    from app.evaluation.sandbox import InMemoryDB

    fake = InMemoryDB()
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
