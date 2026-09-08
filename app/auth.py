"""Authentication dependency backed by Supabase Auth.

Verifies the bearer token on incoming requests by asking Supabase to resolve
it to a user, rather than validating the JWT locally. That keeps the backend
independent of Supabase's signing-key rotation/algorithm and only requires
the public project URL and anon key.
"""
from fastapi import Header, HTTPException
import httpx

from app.config import SUPABASE_ANON_KEY, SUPABASE_URL


class SupabaseNotConfiguredError(Exception):
    """Raised when SUPABASE_URL / SUPABASE_ANON_KEY are missing."""


async def get_current_user(authorization: str = Header(default=None)) -> dict:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(
            status_code=503,
            detail="Supabase authentication is not configured on the server.",
        )

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.split(" ", 1)[1]

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": SUPABASE_ANON_KEY,
            },
        )

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return response.json()
