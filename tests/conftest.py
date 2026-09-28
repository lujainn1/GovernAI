import math
import os
import re
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

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
    from app.observability.metrics import clear_window_cache

    clear_window_cache()  # a previous test's window must not leak into this one
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


class FakeEmbeddingsClient:
    """Offline stand-in for the OpenAI client's embeddings endpoint, so tests
    never call the network. Vectors are hashed bag-of-words (words of 4+
    letters), so texts that share vocabulary get a high cosine similarity and
    unrelated texts a low one - enough to test real retrieval behavior.
    `calls` records every request, to assert when embeddings were (not) paid for."""

    # Large enough that distinct words rarely share a hash bucket (which would
    # make unrelated texts look similar).
    DIM = 4096

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.embeddings = SimpleNamespace(create=self._create)

    def _embed(self, text: str) -> List[float]:
        vector = [0.0] * self.DIM
        for word in re.findall(r"[a-z]{4,}", text.lower()):
            vector[zlib.crc32(word.encode()) % self.DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def _create(self, model: str, input: List[str], **kwargs):
        self.calls.append({"model": model, "input": list(input)})
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=i, embedding=self._embed(text))
                for i, text in enumerate(input)
            ]
        )


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    """Route app.memory's embedding calls to the offline fake (see above).
    Returned so tests can inspect `.calls` or make it fail."""
    client = FakeEmbeddingsClient()
    monkeypatch.setattr("app.memory.get_client", lambda: client)
    return client
