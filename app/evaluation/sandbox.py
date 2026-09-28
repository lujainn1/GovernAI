"""Sandboxed persistence for evaluation runs.

Evaluating the agents means running the real orchestrator, which persists
use cases, reports, and audit entries. An evaluation must never write test
submissions into the production Supabase project, so `sandbox_db` swaps
app.db's functions for an in-memory store seeded from the same
data/*.yaml files scripts/seed_supabase.py loads. The agents' tools, the
orchestrator, and the audit log all run unmodified against it.
"""
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

import yaml

from app import config, db as db_module


class InMemoryDB:
    """In-memory stand-in for app.db. Understands just enough PostgREST
    query syntax (eq. filters, order=col.asc/desc, limit) for the platform's own
    query patterns."""

    def __init__(self):
        self.tables: Dict[str, List[Dict[str, Any]]] = {}

    def _table(self, name: str) -> List[Dict[str, Any]]:
        return self.tables.setdefault(name, [])

    @staticmethod
    def _matches(row: Dict[str, Any], params: Dict[str, Any]) -> bool:
        for key, value in params.items():
            if key in ("select", "order", "limit"):
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
        limit = params.get("limit")
        return rows[: int(limit)] if limit else rows

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


def seed_reference_data(store: InMemoryDB) -> None:
    """Load the shipped policies and risk rules into `store`. Must be
    called while app.db is pointed at `store` (see `sandbox_db`), because
    the repository tools write through app.db."""
    from app.tools.policy_repository import add_policy
    from app.tools.risk_rules import add_risk_rule

    policies = yaml.safe_load(config.POLICIES_FILE.read_text(encoding="utf-8")) or {}
    for policy in policies.get("policies", []):
        add_policy(policy)

    risk_rules = yaml.safe_load(config.RISK_RULES_FILE.read_text(encoding="utf-8")) or {}
    for rule in risk_rules.get("risk_rules", []):
        add_risk_rule(rule)


@contextmanager
def sandbox_db() -> Iterator[InMemoryDB]:
    """Point app.db at a fresh, seeded in-memory store for the duration of
    the block, then restore the real functions - even if the block raises."""
    store = InMemoryDB()
    names = ("select", "insert", "update", "upsert")
    originals = {name: getattr(db_module, name) for name in names}
    for name in names:
        setattr(db_module, name, getattr(store, name))
    try:
        seed_reference_data(store)
        yield store
    finally:
        for name, original in originals.items():
            setattr(db_module, name, original)
