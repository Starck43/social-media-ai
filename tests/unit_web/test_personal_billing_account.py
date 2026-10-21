import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.web import billing


def _request():
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/billing/account",
            "headers": [],
            "state": {"web_user": SimpleNamespace(id=17, is_active=True)},
        }
    )


@pytest.mark.asyncio
async def test_personal_account_includes_workspace_spend_summary(monkeypatch):
    account = SimpleNamespace(
        configured_at=object(),
        is_active=True,
        daily_limit_usd=Decimal("1.00"),
        policy_version="personal-daily-cap-v1",
    )
    summary = {
        "scope": "payer_bound_personal_dispatches",
        "currency": "USD",
        "settled_usd": "0.050000000",
        "reserved_usd": "0",
        "by_workspace": [],
        "complete": True,
    }
    monkeypatch.setattr(billing, "current_personal_account", AsyncMock(return_value=account))
    monkeypatch.setattr(billing, "current_personal_spend_summary", AsyncMock(return_value=summary))

    response = await billing.personal_billing_account(_request())

    assert response.status_code == 200
    assert json.loads(response.body)["spend_today"] == summary
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_personal_account_hides_spend_summary_error(monkeypatch):
    account = SimpleNamespace(
        configured_at=object(),
        is_active=True,
        daily_limit_usd=Decimal("1.00"),
        policy_version="personal-daily-cap-v1",
    )
    monkeypatch.setattr(billing, "current_personal_account", AsyncMock(return_value=account))
    monkeypatch.setattr(
        billing,
        "current_personal_spend_summary",
        AsyncMock(side_effect=RuntimeError("private database detail")),
    )

    response = await billing.personal_billing_account(_request())

    assert response.status_code == 503
    assert json.loads(response.body) == {"ok": False, "error": "spend_summary_unavailable"}
    assert b"private database detail" not in response.body
