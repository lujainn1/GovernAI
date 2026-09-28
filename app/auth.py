"""Authentication dependency backed by Supabase Auth.

Verifies the bearer token on incoming requests by asking Supabase to resolve
it to a user, rather than validating the JWT locally. That keeps the backend
independent of Supabase's signing-key rotation/algorithm and only requires
the public project URL and anon key.

A page load fires several API requests at once, and each one needs the
caller resolved. To keep that from costing a Supabase round trip (and a fresh
TCP+TLS handshake) per request, the HTTP client is shared and a resolved
token is remembered for a short time. The trade-off: a session that is
revoked in Supabase keeps working here for up to `TOKEN_CACHE_TTL_SECONDS`.
"""
import hashlib
import time
from typing import Dict, Optional, Tuple

from fastapi import Header, HTTPException
import httpx

from app.config import SUPABASE_ANON_KEY, SUPABASE_URL

TOKEN_CACHE_TTL_SECONDS = 60
TOKEN_CACHE_MAX_ENTRIES = 512

_client: Optional[httpx.AsyncClient] = None
# sha256(token) -> (expires_at, user). The raw token is never used as a key so
# it isn't kept in memory longer than the request that carried it.
_token_cache: Dict[str, Tuple[float, dict]] = {}


class SupabaseNotConfiguredError(Exception):
    """Raised when SUPABASE_URL / SUPABASE_ANON_KEY are missing."""


def _now() -> float:
    return time.monotonic()


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
    return _client


def _cache_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _cached_user(key: str) -> Optional[dict]:
    entry = _token_cache.get(key)
    if entry is None:
        return None
    expires_at, user = entry
    if expires_at <= _now():
        _token_cache.pop(key, None)
        return None
    return user


def _remember_user(key: str, user: dict) -> None:
    now = _now()
    if len(_token_cache) >= TOKEN_CACHE_MAX_ENTRIES:
        for stale in [k for k, (expires_at, _) in _token_cache.items() if expires_at <= now]:
            del _token_cache[stale]
        if len(_token_cache) >= TOKEN_CACHE_MAX_ENTRIES:
            _token_cache.pop(next(iter(_token_cache)))  # oldest insertion
    _token_cache[key] = (now + TOKEN_CACHE_TTL_SECONDS, user)


async def get_current_user(authorization: str = Header(default=None)) -> dict:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(
            status_code=503,
            detail="Supabase authentication is not configured on the server.",
        )

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.split(" ", 1)[1]
    key = _cache_key(token)

    user = _cached_user(key)
    if user is not None:
        return user

    response = await _get_client().get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={
            "Authorization": f"Bearer {token}",
            "apikey": SUPABASE_ANON_KEY,
        },
    )

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    user = response.json()
    _remember_user(key, user)
    return user
