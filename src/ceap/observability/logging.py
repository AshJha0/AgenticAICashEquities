"""Structured (JSON-lines) logging configuration."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime

from ceap.util.redact import redact

#: correlation id of the HTTP request being served (R4 item 4.6); "-" outside a request
REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("ceap_request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Stamps ``record.request_id`` on every record so every log line carries one."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = REQUEST_ID.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", None) or REQUEST_ID.get(),
            "message": redact(record.getMessage()),
        }
        for key in ("task_id", "execution_id", "step_id", "tool_id", "agent_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)


def configure_logging(level: str = "INFO", json_lines: bool = True) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        JsonFormatter()
        if json_lines
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level.upper())
