"""Safe per-request correlation for HTTP responses and structured logs."""

from __future__ import annotations

import re
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_REQUEST_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}", re.ASCII)
_HEADER_NAME = b"x-request-id"


class RequestContextMiddleware:
    """Accept a bounded opaque request ID or generate one, then echo it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = next(
            (value.decode("latin-1") for name, value in scope.get("headers", []) if name.lower() == _HEADER_NAME),
            "",
        )
        request_id = supplied if _REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
        state = scope.setdefault("state", {})
        state["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(name, value) for name, value in message.get("headers", []) if name.lower() != _HEADER_NAME]
                headers.append((_HEADER_NAME, request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_request_id)
