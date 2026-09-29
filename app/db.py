"""Thin synchronous client for Supabase's PostgREST API.

The backend is the sole trusted intermediary between the platform and the
database (the frontend never talks to Postgres directly), so it authenticates
with the service role key and therefore bypasses Row Level Security
entirely. Every route in app/api.py already requires a valid Supabase
session via app.auth.get_current_user, so authorization is enforced at the
API layer, not by RLS.

This is intentionally a thin wrapper (select/insert/update/upsert) rather
than a full ORM - the platform's data access patterns are simple enough
that a query builder would add more indirection than it saves.
"""
from typing import Any, Dict, List, Optional

import httpx

from app import config

# One pooled client for every call: each request to Supabase would otherwise
# open (and TLS-handshake) a brand-new connection, which dominates the cost of
# the small queries this module makes. httpx.Client is safe to share across the
# threads FastAPI runs sync routes on. The 5s httpx default read timeout is too
# tight for the larger reads (reports, audit log, metrics windows).
_client = httpx.Client(
    timeout=httpx.Timeout(30.0, connect=10.0),
    limits=httpx.Limits(max_keepalive_connections=20, keepalive_expiry=30.0),
)


class SupabaseNotConfiguredError(RuntimeError):
    """Raised when SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are missing."""


def _headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_ROLE_KEY:
        raise SupabaseNotConfiguredError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not configured."
        )
    headers = {
        "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    headers.update(extra or {})
    return headers


def _url(table: str) -> str:
    return f"{config.SUPABASE_URL}/rest/v1/{table}"


# Postgres "invalid input syntax for type ..." - raised when a filter value
# cannot be cast to the column's type, e.g. a path parameter that is not a
# UUID compared against use_cases.id. It means the caller asked for something
# that cannot exist, not that the database is unhealthy, so callers translate
# it into an empty result instead of letting it surface as a 500.
INVALID_TEXT_REPRESENTATION = "22P02"


def is_invalid_value_error(exc: httpx.HTTPStatusError) -> bool:
    """True if PostgREST rejected a filter value as uncastable."""
    if exc.response.status_code != 400:
        return False
    try:
        body = exc.response.json() or {}
    except ValueError:
        return False
    return body.get("code") == INVALID_TEXT_REPRESENTATION


def select(table: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """SELECT rows. `params` uses PostgREST query syntax, e.g.
    {"category": "eq.security", "order": "id.asc"}."""
    response = _client.get(_url(table), headers=_headers(), params={"select": "*", **(params or {})})
    response.raise_for_status()
    return response.json()


def insert(table: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """INSERT a single row. Raises ValueError on a primary-key/unique conflict."""
    response = _client.post(
        _url(table), headers=_headers({"Prefer": "return=representation"}), json=row
    )
    if response.status_code == 409:
        raise ValueError(response.json().get("message", f"Conflict inserting into {table}"))
    response.raise_for_status()
    data = response.json()
    return data[0] if isinstance(data, list) else data


def update(table: str, match: Dict[str, Any], values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """UPDATE rows matching `match` (equality filters) with `values`."""
    params = {key: f"eq.{value}" for key, value in match.items()}
    response = _client.patch(
        _url(table),
        headers=_headers({"Prefer": "return=representation"}),
        params=params,
        json=values,
    )
    response.raise_for_status()
    return response.json()


def upsert(table: str, row: Dict[str, Any], on_conflict: str) -> Dict[str, Any]:
    """INSERT or, on a conflict on the `on_conflict` column(s), UPDATE the existing row."""
    response = _client.post(
        f"{_url(table)}?on_conflict={on_conflict}",
        headers=_headers({"Prefer": "resolution=merge-duplicates,return=representation"}),
        json=row,
    )
    response.raise_for_status()
    data = response.json()
    return data[0] if isinstance(data, list) else data
