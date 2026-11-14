"""Dormant grant console: strict superuser gate, CSRF and service-only writes."""

from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqladmin import Admin
from sqladmin.authorization import Action
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.applications import Starlette

from app.admin.view_modules import plan_model_grants as module
from app.models import PlanModelGrant

__source_only__ = True


def request(*, superuser=True, active=True, token="synthetic-csrf"):
    actor = NS(id=7, is_active=active, is_superuser=superuser)
    return NS(state=NS(admin_user=actor), form=AsyncMock(return_value={"csrf_token": token}))


def values(**changes):
    return {
        "plan": "pro",
        "capability": "text",
        "llm_model_id": 11,
        "priority": 5,
        "is_active": True,
        "is_default": True,
        **changes,
    }


@pytest.mark.parametrize("superuser,active", [(False, True), (False, False), (True, False)])
def test_broad_admin_or_inactive_owner_cannot_see_or_reach_grants(superuser, active):
    view = module.PlanModelGrantAdmin()
    req = request(superuser=superuser, active=active)
    assert not view.is_visible(req)
    assert not view.is_accessible(req)
    assert not view.has_permission(req, Action.CREATE)
    assert not view.has_permission(req, Action.EDIT)


async def test_direct_admin_write_is_denied_before_any_service_or_form_read(monkeypatch):
    view = module.PlanModelGrantAdmin()
    req = request(superuser=False)
    writer = AsyncMock()
    monkeypatch.setattr(module, "save_grant", writer)
    with pytest.raises(HTTPException) as error:
        await view.insert_model(req, values())
    assert error.value.status_code == 403
    writer.assert_not_awaited()
    req.form.assert_not_awaited()


@pytest.mark.parametrize("token", [None, 7, "invalid"])
async def test_csrf_is_verified_even_when_writer_called_directly(monkeypatch, token):
    monkeypatch.setattr(module, "verify_request_token", lambda req, token: False)
    writer = AsyncMock()
    monkeypatch.setattr(module, "save_grant", writer)
    with pytest.raises(HTTPException) as error:
        await module.PlanModelGrantAdmin().insert_model(request(token=token), values())
    assert error.value.status_code == 403
    writer.assert_not_awaited()


def service_session(monkeypatch):
    session = NS()

    @asynccontextmanager
    async def owned():
        yield session

    monkeypatch.setattr(PlanModelGrant.objects, "_get_owned_session", owned)
    monkeypatch.setattr(module, "verify_request_token", lambda req, token: True)
    return session


@pytest.mark.parametrize("editing", [False, True])
async def test_create_and_edit_use_the_same_guarded_service(monkeypatch, editing):
    session = service_session(monkeypatch)
    row = PlanModelGrant(id=19, plan="pro", capability="text", llm_model_id=11)
    writer = AsyncMock(return_value=row)
    monkeypatch.setattr(module, "save_grant", writer)
    view = module.PlanModelGrantAdmin()
    result = (
        await view.update_model(request(), "19", values()) if editing else await view.insert_model(request(), values())
    )
    assert result is row
    writer.assert_awaited_once_with(
        session,
        actor_user_id=7,
        grant_id=19 if editing else None,
        model_id=11,
        plan="pro",
        capability="text",
        priority=5,
        is_active=True,
        is_default=True,
    )


@pytest.mark.parametrize("field", ["owner_user_id", "catalog_id", "model", "managed_credential_ref"])
async def test_forged_relationship_or_custody_fields_are_not_forwarded(monkeypatch, field):
    service_session(monkeypatch)
    writer = AsyncMock()
    monkeypatch.setattr(module, "save_grant", writer)
    with pytest.raises(HTTPException) as error:
        await module.PlanModelGrantAdmin().insert_model(request(), values(**{field: "forged"}))
    assert error.value.status_code == 422
    writer.assert_not_awaited()


async def test_revoked_owner_and_invalid_model_are_bounded_errors(monkeypatch):
    service_session(monkeypatch)
    writer = AsyncMock(side_effect=PermissionError("PRIVATE_SENTINEL"))
    monkeypatch.setattr(module, "save_grant", writer)
    for exception, status in [(PermissionError, 403), (ValueError, 422)]:
        writer.side_effect = exception("PRIVATE_SENTINEL")
        with pytest.raises(HTTPException) as error:
            await module.PlanModelGrantAdmin().insert_model(request(), values())
        assert error.value.status_code == status
        assert "PRIVATE_SENTINEL" not in error.value.detail


async def test_real_form_uses_safe_scalar_options_without_loading_model_relationship(monkeypatch):
    service_session(monkeypatch)
    req = request()
    monkeypatch.setattr(module, "bound_console_request", lambda: req)
    reader = AsyncMock(return_value=[{"id": 11, "provider_name": "Fleet", "model_id": "vendor/text"}])
    monkeypatch.setattr(module, "platform_model_choices", reader)

    class RegisteredGrantAdmin(module.PlanModelGrantAdmin, model=PlanModelGrant):
        pass

    # Real SQLAdmin registration supplies session_maker. No engine is bound;
    # a relationship query would fail, rather than touching any database.
    maker = async_sessionmaker()
    admin = Admin(app=Starlette(), session_maker=maker)
    admin.add_view(RegisteredGrantAdmin)
    [view] = admin._views
    assert view.session_maker is maker
    form = (await view.scaffold_form())()
    assert list(form.llm_model_id.choices) == [(11, "Fleet/vendor/text")]
    assert set(form._fields) == set(view.form_columns)
    assert "model" not in form._fields
    assert reader.await_args.kwargs["include_inactive"] is True  # Existing revoked grants can still be retired.


async def test_raw_grant_delete_is_disabled_for_every_actor():
    view = module.PlanModelGrantAdmin()
    assert view.can_delete is False
    assert view.can_export is False
    assert view.column_details_list == view.column_list
    with pytest.raises(HTTPException) as error:
        await view.delete_model(request(), "19")
    assert error.value.status_code == 403
