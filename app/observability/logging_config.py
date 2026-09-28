"""Structured logging for the `governai.*` loggers.

Each event is one line: a JSON object (default) or key=value text, always
carrying the request id. Events are emitted with `emit(logger, "event_name",
**fields)`; fields must be small scalars - never prompts, documents, or tool
arguments, which can contain sensitive submission text.
"""
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from app import config
from app.observability.context import get_request_id

ROOT_LOGGER = "governai"


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def _payload(record: logging.LogRecord) -> dict:
    data = {
        "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
        "level": record.levelname,
        "logger": record.name,
        "event": record.getMessage(),
        "request_id": getattr(record, "request_id", None),
    }
    data.update(getattr(record, "fields", {}) or {})
    if record.exc_info:
        data["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
    return data


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(_payload(record), default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = _payload(record)
        head = f"{data.pop('ts')} {data.pop('level'):<7} {data.pop('logger')} {data.pop('event')}"
        return head + "".join(f" {key}={value}" for key, value in data.items() if value is not None)


def configure_logging(level: Optional[str] = None, fmt: Optional[str] = None) -> None:
    """Attach one stderr handler to the `governai` logger tree. Safe to call
    more than once; it replaces the handler instead of stacking a new one."""
    logger = logging.getLogger(ROOT_LOGGER)
    for handler in list(logger.handlers):
        if getattr(handler, "_governai", False):
            logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler._governai = True  # type: ignore[attr-defined]
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(TextFormatter() if (fmt or config.LOG_FORMAT) == "text" else JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, (level or config.LOG_LEVEL), logging.INFO))
    logger.propagate = False


def emit(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Log a structured event. `fields` become top-level keys in the output."""
    if logger.isEnabledFor(level):
        logger.log(level, event, extra={"fields": fields})
