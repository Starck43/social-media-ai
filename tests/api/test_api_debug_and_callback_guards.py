"""Analytics debug endpoints need the analytics read right; the public OAuth
callback never reflects raw markup.

The ORM layer is stubbed, so these tests need no database.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1.endpoints import dashboard, social
from app.services.user.auth import get_authenticated_user

DEBUG_PATHS = (
    "/api/v1/dashboard/debug/topic-chain/chain-1",
    "/api/v1/dashboard/debug/analytics",
    "/api/v1/dashboard/debug/analytics/1",
)


def _client(router, prefix: str) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix=prefix)
    app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(id=5)
    return TestClient(app)


@pytest.fixture
def member(monkeypatch):
    """A caller whose workspace role decides the analytics read right."""
    state = SimpleNamespace(allowed=False, checked=[])
    user = SimpleNamespace(id=5, is_active=True, has_perm_for=lambda model, action: False)
    monkeypatch.setattr(deps, "_load_with_rights", AsyncMock(return_value=user))

    def decision(_user, model_name, action):
        state.checked.append((model_name, action))
        return state.allowed

    # The dependency asks the shared policy (`has_permission`), not a
    # workspace-role helper: API, console and web answer with one predicate.
    monkeypatch.setattr(deps, "has_permission", decision)
    return state


@pytest.mark.parametrize("path", DEBUG_PATHS)
def test_debug_endpoint_refuses_member_without_analytics_view(member, monkeypatch, path):
    load = AsyncMock(side_effect=AssertionError("data must not be read before the rights check"))
    monkeypatch.setattr(dashboard.AIAnalytics.objects, "get", load)
    monkeypatch.setattr(dashboard.AIAnalytics.objects, "filter", load)

    response = _client(dashboard.router, "/api/v1/dashboard").get(path)

    assert response.status_code == 403
    assert member.checked == [("aianalytics", deps.ActionType.VIEW)]
    load.assert_not_called()


def test_debug_single_analytics_missing_row_is_404(member, monkeypatch):
    member.allowed = True
    monkeypatch.setattr(dashboard.AIAnalytics.objects, "get", AsyncMock(return_value=None))

    response = _client(dashboard.router, "/api/v1/dashboard").get("/api/v1/dashboard/debug/analytics/99")

    assert response.status_code == 404


def test_debug_analytics_list_returns_rows(member, monkeypatch):
    member.allowed = True
    row = SimpleNamespace(
        id=1, source_id=2, topic_chain_id="c", analysis_date=None, summary_data={"k": 1}, response_payload=None
    )

    class Rows:
        async def limit(self, _count):
            return [row]

    monkeypatch.setattr(dashboard.AIAnalytics.objects, "filter", lambda *a, **k: Rows())

    response = _client(dashboard.router, "/api/v1/dashboard").get("/api/v1/dashboard/debug/analytics")

    assert response.status_code == 200
    assert response.json()[0]["id"] == 1
    assert response.json()[0]["summary_keys"] == ["k"]


def test_callback_escapes_error_query_value():
    payload = "<script>alert(1)</script>"

    response = _client(social.router, "/api/v1/social").get("/api/v1/social/callback", params={"error": payload})

    assert response.status_code == 400
    assert payload not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_callback_escapes_validation_error_message(monkeypatch):
    monkeypatch.setattr(social, "complete_authorization", AsyncMock(side_effect=ValueError('<img src=x onerror="x">')))

    response = _client(social.router, "/api/v1/social").get(
        "/api/v1/social/callback", params={"code": "c", "state": "s"}
    )

    assert response.status_code == 400
    assert "<img" not in response.text
    assert "&lt;img src=x onerror=&quot;x&quot;&gt;" in response.text
