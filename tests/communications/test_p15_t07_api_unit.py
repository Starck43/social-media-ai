"""No-network endpoint checks for recommendation API."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.api.v1.endpoints import communication
from app.models.managers.contact_manager import ContactUnavailable
from app.services.communications.recommend import Recommendation


@pytest.fixture(autouse=True)
def no_db():
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden")

    event.listen(Dialect, "do_connect", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


class Session:
    @asynccontextmanager
    async def begin(self):
        yield


@asynccontextmanager
async def session_factory():
    yield Session()


@pytest.mark.asyncio
async def test_route_returns_advisory_and_opaque_errors(monkeypatch):
    monkeypatch.setattr(communication, "async_session_maker", session_factory)
    operation = AsyncMock(return_value=Recommendation("email", 0.45, ("consented_and_reachable",)))
    monkeypatch.setattr(communication, "recommend_channel", operation)
    result = await communication.get_contact_channel_recommendation(contact_id=3, purpose="marketing")
    assert result.channel == "email" and result.confidence == 0.45
    operation.side_effect = ContactUnavailable("private")
    with pytest.raises(HTTPException) as error:
        await communication.get_contact_channel_recommendation(contact_id=3, purpose="marketing")
    assert error.value.status_code == 404 and "private" not in str(error.value.detail)
    operation.side_effect = PermissionError("private")
    with pytest.raises(HTTPException) as error:
        await communication.get_contact_channel_recommendation(contact_id=3, purpose="marketing")
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_http_requires_purpose_and_uses_router_authorization_doubles(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setattr(communication, "async_session_maker", session_factory)
    operation = AsyncMock(return_value=Recommendation(None, 0.0, ("no_consented_reachable_channel",)))
    monkeypatch.setattr(communication, "recommend_channel", operation)
    app = FastAPI()
    app.include_router(communication.router, prefix="/api/v1/communication")

    async def allow():
        return None

    for route in communication.router.routes:
        if hasattr(route, "dependant"):
            for dependency in route.dependant.dependencies:
                app.dependency_overrides[dependency.call] = allow
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        url = "/api/v1/communication/contacts/3/recommend-channel"
        missing = await client.get(url)
        assert missing.status_code == 422
        invalid = await client.get(url, params={"purpose": "other"})
        assert invalid.status_code == 422
        ok = await client.get(url, params={"purpose": "greeting"})
        assert ok.status_code == 200 and ok.json()["channel"] is None
        assert operation.await_args.args[1:] == (3, "greeting")
