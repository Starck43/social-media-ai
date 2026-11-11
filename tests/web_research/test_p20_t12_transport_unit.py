"""Focused no-network regressions for P20-T12's outbound transport boundary."""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from app.core.outbound import OutboundTarget
from app.services.web_research import RawHttpResponse, WebResearchRefusal, fetch_public_page, fetcher

__source_only__ = True


def _resolver(host: str, port: int) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def test_public_url_keeps_explicit_scheme_default_port_and_ipv6_brackets():
    def ipv6_resolver(host: str, port: int) -> list[tuple]:
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", port, 0, 0))]

    url, target = fetcher.validate_public_url("https://[2606:4700:4700::1111]:443/path", resolver=ipv6_resolver)

    assert url == "https://[2606:4700:4700::1111]:443/path"
    assert target.host == "2606:4700:4700::1111"


def test_pinned_request_uses_validated_ip_but_original_http_host_and_tls_name():
    target = OutboundTarget("research.example", 443, ("93.184.216.34",))
    with httpx.Client(verify=True, trust_env=False) as client:
        request = fetcher._build_pinned_request(client, "https://research.example/a?q=1", target)

    assert request.url.host == "93.184.216.34"
    assert request.url.path == "/a"
    assert request.url.query == b"q=1"
    assert request.headers["host"] == "research.example"
    assert request.extensions["sni_hostname"] == b"research.example"


def test_pinned_request_retains_ipv6_and_explicit_default_port():
    target = OutboundTarget("research.example", 443, ("2606:4700:4700::1111",))
    with httpx.Client(verify=True, trust_env=False) as client:
        request = fetcher._build_pinned_request(client, "https://research.example:443/", target)

    assert request.url.host == "2606:4700:4700::1111"
    assert request.url.port == 443
    assert request.headers["host"] == "research.example"


@pytest.mark.asyncio
async def test_default_http_transport_sends_request_with_tls_validation_enabled(monkeypatch):
    observed: dict[str, object] = {}

    class Response:
        status_code = 200
        headers = {"content-type": "text/plain", "content-length": "2"}

        async def aiter_bytes(self):
            yield b"ok"

        async def aclose(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            observed["client_options"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        def build_request(self, method, url, **kwargs):
            return httpx.Request(method, url, **kwargs)

        async def send(self, request, stream):
            observed["request"] = request
            observed["stream"] = stream
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    response = await fetcher._default_http_transport(
        "https://research.example/page", 1.0, 100, target=OutboundTarget("research.example", 443, ("93.184.216.34",))
    )

    options = observed["client_options"]
    request = observed["request"]
    assert options["verify"] is True
    assert options["trust_env"] is False
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "research.example"
    assert request.extensions["sni_hostname"] == b"research.example"
    assert response.body == b"ok"


@pytest.mark.asyncio
async def test_default_transport_checks_robots_before_fetching(monkeypatch):
    calls: list[tuple[str, str, tuple[str, ...]]] = []

    async def pinned(url, timeout, limit, *, target):
        calls.append((url, target.host, target.addresses))
        if url.endswith("/robots.txt"):
            return RawHttpResponse(200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /private")
        return RawHttpResponse(200, {"content-type": "text/html"}, b"<title>ok</title><p>public text</p>")

    monkeypatch.setattr(fetcher, "_default_http_transport", pinned)
    with pytest.raises(WebResearchRefusal) as exc:
        await fetch_public_page("https://research.example/private", resolver=_resolver)

    assert exc.value.code == "robots_disallowed"
    assert len(calls) == 1
    assert calls[0][0].endswith("/robots.txt")
    assert calls[0][1:] == ("research.example", ("93.184.216.34",))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 403, 500, 503])
async def test_robots_redirect_and_server_failures_fail_closed(monkeypatch, status):
    async def pinned(url, timeout, limit, *, target):
        return RawHttpResponse(status, {"location": "https://other.example/robots.txt"}, b"")

    monkeypatch.setattr(fetcher, "_default_http_transport", pinned)
    with pytest.raises(WebResearchRefusal) as exc:
        await fetch_public_page("https://research.example/page", resolver=_resolver)

    assert exc.value.code == "provider_unavailable"


@pytest.mark.asyncio
async def test_default_transport_uses_one_absolute_deadline_across_robots_and_page():
    calls = 0

    async def transport(url: str, timeout: float, limit: int) -> RawHttpResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(0.015)
            return RawHttpResponse(404, {}, b"")
        await asyncio.sleep(0.05)
        return RawHttpResponse(200, {"content-type": "text/html"}, b"<p>late</p>")

    with pytest.raises(WebResearchRefusal) as exc:
        await fetch_public_page(
            "https://research.example/page", resolver=_resolver, transport=transport, timeout_seconds=0.04
        )

    assert exc.value.code == "fetch_timeout"
    assert calls == 2


@pytest.mark.asyncio
async def test_unconfined_chromium_page_runner_is_never_called():
    called = False

    async def page_runner(url, timeout, limit):
        nonlocal called
        called = True
        return RawHttpResponse(200, {"content-type": "text/html"}, b"<p>content</p>")

    engine = fetcher.ChromiumUnderHoodEngine(page_runner=page_runner)
    with pytest.raises(WebResearchRefusal) as exc:
        await engine.fetch_rendered_page("https://research.example/page", resolver=_resolver)

    assert exc.value.code == "provider_unavailable"
    assert called is False


def test_long_page_with_password_input_is_still_a_login_wall():
    text = "ordinary page content " * 20
    html = f'<html><body><main>{text}<input type="password"></main></body></html>'

    with pytest.raises(WebResearchRefusal) as exc:
        fetcher.sanitize_and_extract_page(html)

    assert exc.value.code == "login_or_captcha_blocked"
