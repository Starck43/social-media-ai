"""Focused company-connector chat reference projection contracts."""

import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from starlette.requests import Request

from app.api.v1.endpoints.files_connectors import SearchReleaseResponse
from app.core.config import settings
from app.services.local_connector.channel import ConnectorChannels
from app.services.local_connector.gateway import SearchDispatch
from app.services.local_connector.wire import FileReference, SearchHit
from app.web import chat


class FakeCsrf:
    def verify_token(self, token):
        return token == "valid-csrf"


class FakeStore:
    def __init__(self, devices):
        self.devices = devices
        self.authorized = []

    async def list_owned(self, *, owner_id, limit):
        assert owner_id == 7
        assert limit == 100
        return self.devices

    async def authorize(self, **kwargs):
        self.authorized.append(kwargs)


class FakeGateway:
    def __init__(self, devices):
        self.store = FakeStore(devices)
        self.channels = ConnectorChannels()
        self.channels.sessions[11] = object()
        self.search_calls = []
        self.dispatch = SearchDispatch(
            payload={
                "hits": [
                    SearchHit(
                        reference=FileReference(
                            object_id="object-1",
                            root_id="company-root",
                            path="/company/annual-report.docx",
                            page=4,
                        ),
                        rank=5,
                        snippet="SYNTHETIC_SNIPPET_MUST_NOT_RETURN",
                        data_boundary="company",
                    ).model_dump()
                ],
                "partial": False,
            },
            snapshots={11: (3, 7, 11, 1, 2, 3)},
        )

    async def search(self, *, owner_id, device_ids, args):
        self.search_calls.append((owner_id, device_ids, args))
        return self.dispatch


def device(*, device_id=11, roots=None, revoked_at=None):
    return SimpleNamespace(
        id=device_id,
        revoked_at=revoked_at,
        cloud_roots=roots if roots is not None else ["company-root"],
        device_name_encrypted="Synthetic device",
    )


def make_request(*, fields, csrf="valid-csrf", actor=True, access=True, content_length=None):
    values = {"_csrf": csrf, **fields}
    body = urlencode(values, doseq=True).encode("ascii")
    state = {
        "web_perms": SimpleNamespace(can_open_chat=access),
        "web_user": SimpleNamespace(id=7) if actor else None,
        "tenant_id": 3 if actor else None,
    }
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/app/chat/connector-search",
        "headers": [
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", str(len(body) if content_length is None else content_length).encode("ascii")),
        ],
        "query_string": b"",
        "state": state,
        "app": SimpleNamespace(state=SimpleNamespace(csrf_manager=FakeCsrf())),
    }
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


async def send_response(response, scope):
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.disconnect"}

    await response(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages)
    return start["status"], body


@pytest.fixture
def connector_gateway(monkeypatch):
    gateway = FakeGateway([device()])
    monkeypatch.setattr(settings, "LOCAL_CONNECTOR_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_CONNECTOR_SECURITY_REVIEWED", True)
    return gateway


@pytest.mark.asyncio
async def test_chat_search_returns_only_company_references_under_release_fence(connector_gateway, monkeypatch):
    from app.services.local_connector import runtime

    monkeypatch.setattr(runtime, "get_gateway", lambda: connector_gateway)
    request = make_request(fields={"query": "annual report", "device_id": "11", "roots": ["company-root"]})

    response = await chat.chat_connector_search(request)

    assert isinstance(response, SearchReleaseResponse)
    assert connector_gateway.search_calls[0][0:2] == (7, [11])
    assert connector_gateway.search_calls[0][2].roots == ["company-root"]
    status, body = await send_response(response, request.scope)
    payload = json.loads(body)
    assert status == 200
    assert payload == {
        "references": [
            {
                "object_id": "object-1",
                "root_id": "company-root",
                "path": "/company/annual-report.docx",
                "page": 4,
                "sheet": None,
            }
        ],
        "partial": False,
    }
    assert b"SYNTHETIC_SNIPPET_MUST_NOT_RETURN" not in body
    assert connector_gateway.store.authorized == [
        {
            "owner_id": 7,
            "device_id": 11,
            "roots": ["company-root"],
            "previous": (3, 7, 11, 1, 2, 3),
        }
    ]
    assert not connector_gateway.channels.release_fence.locked()


@pytest.mark.asyncio
async def test_chat_search_rejects_missing_auth_and_bad_csrf_before_gateway(connector_gateway, monkeypatch):
    from app.services.local_connector import runtime

    monkeypatch.setattr(runtime, "get_gateway", lambda: pytest.fail("gateway must not be touched"))
    bad_csrf = make_request(fields={"query": "q", "device_id": "11", "roots": ["company-root"]}, csrf="bad")
    response = await chat.chat_connector_search(bad_csrf)
    assert response.status_code == 403
    assert b"SYNTHETIC_SNIPPET" not in response.body

    no_actor = make_request(fields={"query": "q", "device_id": "11", "roots": ["company-root"]}, actor=False)
    response = await chat.chat_connector_search(no_actor)
    assert response.status_code == 401

    forbidden = make_request(fields={"query": "q", "device_id": "11", "roots": ["company-root"]}, access=False)
    response = await chat.chat_connector_search(forbidden)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_chat_search_denies_foreign_device_and_unconsented_root(connector_gateway, monkeypatch):
    from app.services.local_connector import runtime

    monkeypatch.setattr(runtime, "get_gateway", lambda: connector_gateway)
    foreign_device = make_request(fields={"query": "q", "device_id": "99", "roots": ["company-root"]})
    response = await chat.chat_connector_search(foreign_device)
    assert response.status_code == 404
    assert connector_gateway.search_calls == []

    local_root = make_request(fields={"query": "q", "device_id": "11", "roots": ["local-only-root"]})
    response = await chat.chat_connector_search(local_root)
    assert response.status_code == 404
    assert connector_gateway.search_calls == []


@pytest.mark.asyncio
async def test_chat_search_bounds_form_and_root_count_before_gateway(connector_gateway, monkeypatch):
    from app.services.local_connector import runtime

    monkeypatch.setattr(runtime, "get_gateway", lambda: pytest.fail("gateway must not be touched"))
    too_many_roots = make_request(
        fields={"query": "q", "device_id": "11", "roots": [f"root-{index}" for index in range(17)]}
    )
    response = await chat.chat_connector_search(too_many_roots)
    assert response.status_code == 400

    oversized = make_request(fields={"query": "x" * 66_000, "device_id": "11", "roots": ["company-root"]})
    response = await chat.chat_connector_search(oversized)
    assert response.status_code == 413

    oversized_with_short_length = make_request(
        fields={"query": "x" * 66_000, "device_id": "11", "roots": ["company-root"]},
        content_length=1,
    )
    response = await chat.chat_connector_search(oversized_with_short_length)
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_chat_search_is_default_off_and_sanitizes_malformed_gateway_payload(connector_gateway, monkeypatch):
    from app.services.local_connector import runtime

    monkeypatch.setattr(settings, "LOCAL_CONNECTOR_ENABLED", False)
    monkeypatch.setattr(settings, "LOCAL_CONNECTOR_SECURITY_REVIEWED", False)
    monkeypatch.setattr(runtime, "get_gateway", lambda: pytest.fail("default-off must not create the gateway"))
    options = await chat._company_connector_search_options(owner_id=7)
    assert options == {"enabled": False, "devices": []}
    disabled = await chat.chat_connector_search(
        make_request(fields={"query": "q", "device_id": "11", "roots": ["company-root"]})
    )
    assert disabled.status_code == 503

    monkeypatch.setattr(settings, "LOCAL_CONNECTOR_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_CONNECTOR_SECURITY_REVIEWED", True)
    monkeypatch.setattr(runtime, "get_gateway", lambda: connector_gateway)
    connector_gateway.dispatch.payload = {"hits": [{"snippet": "SECRET-MALFORMED"}], "partial": False}
    malformed = await chat.chat_connector_search(
        make_request(fields={"query": "q", "device_id": "11", "roots": ["company-root"]})
    )
    assert malformed.status_code == 503
    assert b"SECRET-MALFORMED" not in malformed.body


def test_chat_template_keeps_references_in_text_only_projection():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    template = (root / "app/web/templates/web/chat.html").read_text()
    script = (root / "app/static/js/chat.js").read_text()
    assert "data-company-reference-results" in template
    assert 'x-text="companyReferenceLabel(reference)"' in template
    assert 'x-html="companyReferenceLabel(reference)"' not in template
    assert "fetch('/app/chat/connector-search'" in script
