"""Pure-ASGI middleware: correlation id, HTTP metrics, request body limit (R4 items 4.4, 4.6, 4.8)."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from ceap.observability.logging import REQUEST_ID
from ceap.observability.metrics import metrics

log = logging.getLogger("ceap.http")
Scope = dict[str, Any]
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
DEFAULT_MAX_BODY_BYTES = 65_536


class _BodyTooLargeError(Exception):
    pass


async def _send_json(send: Any, status: int, detail: str, request_id: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"x-request-id", request_id.encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RequestContextMiddleware:
    """Assigns ``X-Request-ID`` (a safe incoming value is kept), bounds the body, records HTTP metrics."""

    def __init__(self, app: Any) -> None:
        self.app = app

    @staticmethod
    def _max_body(scope: Scope) -> int:
        app = scope.get("app")
        platform = getattr(getattr(app, "state", None), "platform", None)
        return int(getattr(getattr(platform, "settings", None), "max_body_bytes", DEFAULT_MAX_BODY_BYTES))

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = REQUEST_ID.set(request_id)
        started = time.perf_counter()
        status_holder = {"status": 500, "started": False}
        limit = self._max_body(scope)

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                status_holder["started"] = True
                message = {
                    **message,
                    "headers": [*message.get("headers", []), (b"x-request-id", request_id.encode())],
                }
            await send(message)

        received = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal received
            message: dict[str, Any] = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLargeError
            return message

        try:
            declared = headers.get(b"content-length")
            if declared is not None and declared.isdigit() and int(declared) > limit:
                status_holder["status"] = 413
                await _send_json(send, 413, f"request body exceeds {limit} bytes", request_id)
                return
            try:
                await self.app(scope, limited_receive, send_wrapper)
            except _BodyTooLargeError:
                status_holder["status"] = 413
                if not status_holder["started"]:
                    await _send_json(send, 413, f"request body exceeds {limit} bytes", request_id)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            code = int(status_holder["status"])
            method = scope.get("method", "GET")
            metrics.inc("ceap_http_requests_total", method=method, route=template, status=str(code))
            if code >= 400:
                metrics.inc("ceap_http_errors_total", route=template, status_class=f"{code // 100}xx")
            elapsed = time.perf_counter() - started
            metrics.observe("ceap_http_request_duration_seconds", elapsed, method=method, route=template)
            log.info("%s %s -> %d in %.1f ms", method, template, code, elapsed * 1000.0)
            REQUEST_ID.reset(token)
