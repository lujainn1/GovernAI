"""Per-request correlation id.

Every HTTP request (and CLI command) gets a request id that is attached to
each log line and stored on every agent run it causes, so one id ties the
access log, the agent traces, and the audit trail of a submission together.
"""
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

# An id supplied by a caller is echoed into logs, so accept only a safe shape
# (no spaces, newlines, or quotes that could forge or break log lines).
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def new_request_id() -> str:
    return uuid.uuid4().hex


def sanitize_request_id(value: Optional[str]) -> Optional[str]:
    return value if value and _SAFE_ID.match(value) else None


def get_request_id() -> Optional[str]:
    return request_id_var.get()


@contextmanager
def request_context(request_id: Optional[str] = None) -> Iterator[str]:
    """Bind a request id for the duration of the block (a fresh one if none given)."""
    rid = sanitize_request_id(request_id) or new_request_id()
    token = request_id_var.set(rid)
    try:
        yield rid
    finally:
        request_id_var.reset(token)
