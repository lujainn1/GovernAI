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


def select(table: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """SELECT rows. `params` uses PostgREST query syntax, e.g.
    {"category": "eq.security", "order": "id.asc"}."""
    response = httpx.get(_url(table), headers=_headers(), params={"select": "*", **(params or {})})
    response.raise_for_status()
    return response.json()


def insert(table: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """INSERT a single row. Raises ValueError on a primary-key/unique conflict."""
    response = httpx.post(
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
    response = httpx.patch(
        _url(table),
        headers=_headers({"Prefer": "return=representation"}),
        params=params,
        json=values,
    )
    response.raise_for_status()
    return response.json()


def upsert(table: str, row: Dict[str, Any], on_conflict: str) -> Dict[str, Any]:
    """INSERT or, on a conflict on the `on_conflict` column(s), UPDATE the existing row."""
    response = httpx.post(
        f"{_url(table)}?on_conflict={on_conflict}",
        headers=_headers({"Prefer": "resolution=merge-duplicates,return=representation"}),
        json=row,
    )
    response.raise_for_status()
    data = response.json()
    return data[0] if isinstance(data, list) else data
