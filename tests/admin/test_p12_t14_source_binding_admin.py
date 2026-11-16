"""Source SQLAdmin must use the same fresh picker-binding guard as manager writes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqladmin.models import ModelView
from sqlalchemy import Select
from starlette.requests import Request

import app.admin.view_modules.sources as source_views
from app.admin.view_modules.sources import SourceAdmin

__source_only__ = True


def _request():
    return Request({"type": "http", "method": "POST", "path": "/admin/source/edit/7", "headers": []})


def _binding(grant_id):
    return {
        "mode": "user",
        "token_owner": 7,
        "telegram_authorization": {"grant_id": grant_id, "authorization_revision": 2},
    }


class _Result:
    def __init__(self, row):
        self.row = row

    def scalar(self):
        return self.row


def _row(*, params=None, tenant_id=1, platform_id=2):
    return SimpleNamespace(
        id=7,
        tenant_id=tenant_id,
        platform_id=platform_id,
        external_id="@synthetic",
        owner_user_id=7,
        params=_binding(11) if params is None else params,
    )


async def test_sqladmin_create_refuses_a_forged_picker_binding_before_storage(monkeypatch):
    base_insert = AsyncMock(side_effect=AssertionError("forged source reached SQLAdmin storage"))
    monkeypatch.setattr(ModelView, "insert_model", base_insert)
    view = SourceAdmin.__new__(SourceAdmin)
    with pytest.raises(HTTPException) as refused:
        await view.insert_model(_request(), {"params": _binding(11)})
    assert refused.value.status_code == 403
    assert refused.value.detail == "telegram_source_picker_required"
    base_insert.assert_not_awaited()


@pytest.mark.parametrize(
    "data",
    [
        {"params": _binding(12)},
        {"params": {}},
        {"external_id": "@another"},
        {"platform": "3"},
        {"tenant": "2"},
    ],
)
async def test_sqladmin_edit_checks_the_fresh_locked_binding(data, monkeypatch):
    # Simulate the picker changing the row after SQLAdmin loaded its edit form.
    stale = _row()
    current = _row(params=_binding(22))
    session = SimpleNamespace(scalar=AsyncMock(return_value=current))
    monkeypatch.setattr(source_views, "async_object_session", lambda model: session)

    view = SourceAdmin.__new__(SourceAdmin)
    with pytest.raises(HTTPException) as refused:
        await view.on_model_change(data, stale, False, _request())
    assert refused.value.status_code == 403
    assert refused.value.detail == "telegram_source_picker_required"
    session.scalar.assert_awaited_once()
    statement = session.scalar.await_args.args[0]
    assert isinstance(statement, Select)
    assert statement._for_update_arg is not None
    assert statement.get_execution_options().get("populate_existing") is True


async def test_sqladmin_edit_allows_unrelated_changes_after_refresh(monkeypatch):
    stale = _row()
    current = _row()
    session = SimpleNamespace(scalar=AsyncMock(return_value=current))
    monkeypatch.setattr(source_views, "async_object_session", lambda model: session)

    await SourceAdmin.__new__(SourceAdmin).on_model_change({"name": "Renamed"}, stale, False, _request())
    session.scalar.assert_awaited_once()
    statement = session.scalar.await_args.args[0]
    assert statement._for_update_arg is not None
    assert statement.get_execution_options().get("populate_existing") is True


async def test_sqladmin_edit_fails_closed_without_its_write_session(monkeypatch):
    monkeypatch.setattr(source_views, "async_object_session", lambda model: None)
    with pytest.raises(HTTPException) as refused:
        await SourceAdmin.__new__(SourceAdmin).on_model_change({"name": "Renamed"}, _row(), False, _request())
    assert refused.value.status_code == 403
    assert refused.value.detail == "telegram_source_update_unavailable"
