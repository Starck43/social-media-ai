"""Source-only manager contract: journal access fails closed without its DB guards."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models.managers.workspace_offboarding_manager import WorkspaceOffboardingManager

__source_only__ = True


class _Result:
    def __init__(self, *, actor_id=None, guards=()):
        self.actor_id = actor_id
        self.guards = tuple(guards)

    def scalar_one_or_none(self):
        return self.actor_id

    def all(self):
        return list(self.guards)


_GUARD_BODY = "BEGIN RAISE EXCEPTION 'Workspace offboarding evidence is immutable' USING ERRCODE = '55000'; END;"


def _guard(name, mask, schema, *, body=_GUARD_BODY, enabled="O", security_definer=False):
    return (
        name,
        mask,
        enabled,
        "guard_workspace_offboarding_mutation",
        schema,
        body,
        security_definer,
        None,
        0,
        "plpgsql",
        "trigger",
    )


class _Session:
    def __init__(self, *, actor_id=7, guards=()):
        self.actor_id = actor_id
        self.guards = tuple(guards)
        self.calls = 0

    async def execute(self, statement, params=None):
        self.calls += 1
        if self.calls == 1:
            return _Result(actor_id=self.actor_id)
        assert params["table"] == "workspace_offboarding_events"
        return _Result(guards=self.guards)


@contextmanager
def _superuser_scope():
    actor = SimpleNamespace(id=7)
    with tenant_scope(bypass=True), permission_scope(actor):
        yield


@pytest.mark.asyncio
async def test_ensure_available_fails_closed_when_table_or_guards_are_missing():
    manager = WorkspaceOffboardingManager()
    session = _Session()

    with _superuser_scope(), pytest.raises(RuntimeError, match="apply its database migration"):
        await manager.ensure_available(session=session)

    assert session.calls == 2


@pytest.mark.asyncio
async def test_ensure_available_accepts_both_enabled_schema_local_guards():
    manager = WorkspaceOffboardingManager()
    from app.core.config import settings

    session = _Session(
        guards=(
            _guard("workspace_offboarding_events_immutable", 27, settings.DB_SCHEMA),
            _guard("workspace_offboarding_events_no_truncate", 34, settings.DB_SCHEMA, enabled="A"),
        )
    )

    with _superuser_scope():
        await manager.ensure_available(session=session)

    assert session.calls == 2


@pytest.mark.asyncio
async def test_ensure_available_rejects_disabled_or_wrong_schema_guard():
    manager = WorkspaceOffboardingManager()
    session = _Session(
        guards=(
            _guard("workspace_offboarding_events_immutable", 27, "test_schema", enabled="D"),
            _guard("workspace_offboarding_events_no_truncate", 34, "public"),
        )
    )

    with _superuser_scope(), pytest.raises(RuntimeError, match="apply its database migration"):
        await manager.ensure_available(session=session)


@pytest.mark.asyncio
async def test_ensure_available_rejects_same_name_noop_trigger_function():
    manager = WorkspaceOffboardingManager()
    from app.core.config import settings

    session = _Session(
        guards=(
            _guard(
                "workspace_offboarding_events_immutable",
                27,
                settings.DB_SCHEMA,
                body="BEGIN RETURN NEW; END;",
            ),
            _guard("workspace_offboarding_events_no_truncate", 34, settings.DB_SCHEMA),
        )
    )

    with _superuser_scope(), pytest.raises(RuntimeError, match="apply its database migration"):
        await manager.ensure_available(session=session)
