"""Search release and device policy changes share a bounded ASGI fence."""

import asyncio
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.api.v1.endpoints.files_connectors import SearchReleaseResponse, revoke
from app.services.local_connector.channel import ConnectorChannels
from app.services.local_connector.gateway import SearchDispatch
from app.services.local_connector.wire import ConnectorUnavailable

PRIVATE_PATH = "/private/customer-contract.docx"


class FakeStore:
    def __init__(self, *, events=None, denied=False):
        self.events = events if events is not None else []
        self.denied = denied

    async def authorize(self, **_kwargs):
        self.events.append("authorize")
        if self.denied:
            raise ConnectorUnavailable()

    async def revoke(self, **_kwargs):
        self.events.append("revoke")


class FakeGateway:
    def __init__(self, *, events=None, denied=False):
        self.store = FakeStore(events=events, denied=denied)
        self.channels = ConnectorChannels()


def request_for(*, api_user=None):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/files/search",
            "headers": [],
            "query_string": b"",
            "state": {"request_id": "p18t02-test-request", "api_user": api_user},
        }
    )


def search_response(gateway):
    dispatch = SearchDispatch(
        payload={
            "hits": [
                {
                    "reference": {
                        "object_id": "object-1",
                        "root_id": "root-1",
                        "path": PRIVATE_PATH,
                        "page": 1,
                        "sheet": None,
                    },
                    "rank": 3,
                    "snippet": "private snippet marker",
                    "data_boundary": "company",
                    "evidence_kind": "local_search_observation",
                }
            ],
            "partial": False,
        },
        snapshots={7: (2, 5, 7, 11, 13, 17)},
    )
    return SearchReleaseResponse(
        request=request_for(),
        gateway=gateway,
        owner_id=5,
        roots=["root-1"],
        dispatch=dispatch,
    )


async def empty_receive():
    return {"type": "http.disconnect"}


@pytest.mark.asyncio
async def test_search_response_reauthorizes_inside_fence_and_denies_before_payload():
    gateway = FakeGateway(denied=True)
    response = search_response(gateway)
    messages = []

    async def send(message):
        messages.append(message)

    await response(request_for().scope, empty_receive, send)

    assert gateway.store.events == ["authorize"]
    assert messages[0]["status"] == 404
    body = b"".join(message.get("body", b"") for message in messages)
    assert PRIVATE_PATH.encode() not in body
    assert b"private snippet marker" not in body
    assert not gateway.channels.release_fence.locked()


@pytest.mark.asyncio
async def test_device_revoke_waits_until_authorized_search_body_is_sent():
    events = []
    gateway = FakeGateway(events=events)
    response = search_response(gateway)
    body_started = asyncio.Event()
    allow_body_send = asyncio.Event()

    async def send(message):
        if message["type"] == "http.response.body":
            events.append("body-start")
            body_started.set()
            await allow_body_send.wait()
            events.append("body-complete")

    response_task = asyncio.create_task(response(request_for().scope, empty_receive, send))
    await body_started.wait()

    revoke_task = asyncio.create_task(revoke(request_for(api_user=SimpleNamespace(id=5)), device_id=7, gw=gateway))
    await asyncio.sleep(0)
    assert "revoke" not in events

    allow_body_send.set()
    await response_task
    await revoke_task

    assert events == ["authorize", "body-start", "body-complete", "revoke"]
    assert not gateway.channels.release_fence.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("send_error", [OSError("client disconnected"), TimeoutError("send timed out")])
async def test_send_failure_releases_release_fence(send_error):
    gateway = FakeGateway()
    response = search_response(gateway)

    async def send(_message):
        raise send_error

    with pytest.raises(type(send_error)):
        await response(request_for().scope, empty_receive, send)

    assert not gateway.channels.release_fence.locked()
