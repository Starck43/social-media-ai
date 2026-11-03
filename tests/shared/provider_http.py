"""Synthetic, opt-in provider doubles. No actual provider response is implied."""

from __future__ import annotations

import socket
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import httpx


@dataclass(frozen=True)
class ResponseSpec:
    status: int
    body: Any = None
    headers: dict[str, str] | None = None


def mock_transport(routes: dict[tuple[str, str], ResponseSpec]) -> httpx.MockTransport:
    """Match exact method + URL, rejecting every unregistered request."""

    def handle(request: httpx.Request) -> httpx.Response:
        key = (request.method.upper(), str(request.url))
        if key not in routes:
            raise AssertionError(f"Unexpected synthetic provider request: {request.method} {request.url.host}")
        spec = routes[key]
        return httpx.Response(spec.status, json=spec.body, headers=spec.headers, request=request)

    return httpx.MockTransport(handle)


@contextmanager
def deny_network():
    """Tripwire for sync socket connections and DNS; not an OS-level sandbox."""

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Network is disabled for synthetic provider tests")

    with (
        patch.object(socket.socket, "connect", blocked),
        patch.object(socket.socket, "connect_ex", blocked),
        patch.object(socket, "create_connection", blocked),
        patch.object(socket, "getaddrinfo", blocked),
    ):
        yield


class MailDouble:
    """Scripted IMAP/SMTP command double: never opens a connection."""

    def __init__(self, responses: dict[str, Any]):
        self._responses = dict(responses)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def command(self, name: str, *args: Any) -> Any:
        name = name.upper()
        if name not in self._responses:
            raise AssertionError(f"Unexpected synthetic mail command: {name}")
        self.calls.append((name, args))
        return self._responses[name]


def synthetic_provider_routes():
    """Load non-secret examples; replace or extend routes for the tested adapter."""
    import json
    from pathlib import Path

    path = Path(__file__).parent / "fixtures/business_providers.json"
    examples = json.loads(path.read_text(encoding="utf-8"))
    routes = {
        (item["method"], item["url"]): ResponseSpec(item["status"], item["body"]) for item in examples["http"].values()
    }
    return routes, examples["mail"]
