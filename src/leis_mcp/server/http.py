"""HTTP hardening for the streamable-http transport: optional bearer key and an open health check."""

import hmac
import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

HEALTH_PATH = "/health"


class ApiKeyMiddleware:
    """Pure ASGI middleware: `GET /health` is always open; with a key, everything else needs `Bearer <key>`.

    Pure ASGI (not `BaseHTTPMiddleware`) so the SSE streams of the MCP transport pass through untouched.
    The comparison is constant-time and the 401 body never says why the request was rejected.
    """

    def __init__(self, app: ASGIApp, api_key: str | None):
        self._app = app
        self._expected = f"Bearer {api_key}".encode() if api_key else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if scope.get("path") == HEALTH_PATH and scope.get("method") in ("GET", "HEAD"):
            await self._respond(send, 200, {"status": "ok"})
            return
        if self._expected is not None and not self._authorized(scope):
            await self._respond(send, 401, {"error": "unauthorized"}, [(b"www-authenticate", b"Bearer")])
            return
        await self._app(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        assert self._expected is not None
        values = [value for name, value in scope.get("headers", []) if name.lower() == b"authorization"]
        return len(values) == 1 and hmac.compare_digest(values[0], self._expected)

    @staticmethod
    async def _respond(send: Send, status: int, payload: dict[str, str], extra: list | None = None) -> None:
        body = json.dumps(payload).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        await send({"type": "http.response.start", "status": status, "headers": headers + (extra or [])})
        await send({"type": "http.response.body", "body": body})
