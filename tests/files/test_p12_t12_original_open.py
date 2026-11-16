"""Original-open orchestration with actual DB guards and injected fake adapters."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, update

from app.core.database import async_session_maker
from app.integrations.file_workspace.original_open import (
    OPEN_TTL,
    NativeLinkPolicy,
    NativeOriginalTarget,
    OriginalOpenUnavailable,
)
from app.models import FileCatalogObject, TenantUser, User, WorkspaceConnection, WorkspaceConnectionSecret
from app.models.managers.file_catalog_manager import FileCatalogManager
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace import original_open as service
from app.utils.crypto import encrypt_secret
from tests.shared.file_catalog_fixtures import actor, admit, configure, data  # noqa: F401

pytestmark = pytest.mark.tenancy
manager = FileCatalogManager()
URL = "https://native.example.invalid/client/files/opaque-file"


class FakeResolver:
    link_policy = NativeLinkPolicy("native.example.invalid", "/client/files/")

    def __init__(self, hook=None):
        self.calls = []
        self.hook = hook

    async def resolve(self, context):
        self.calls.append(context)
        if self.hook:
            await self.hook(context)
        return NativeOriginalTarget(context, URL, datetime.now(timezone.utc), context.issued_at + OPEN_TTL)


async def file_id(args, *, kind="file"):
    await admit(args, "private.txt", kind=kind)
    return (await manager.list_page(**args))["items"][0]["id"]


async def test_current_file_target_and_catalog_remain_honest(data):
    resolver = FakeResolver()
    with actor(data) as args:
        object_id = await file_id(args)
        result = await service.resolve_original(**args, object_id=object_id, resolver=resolver)
        assert result.url == URL and result.version_scope == "current" and result.content_version_known is False
        assert len(resolver.calls) == 1 and resolver.calls[0].path == "disk:/work/private.txt"
        detail = await manager.detail(**args, object_id=object_id)
        assert (
            detail["original_open_available"] is detail["content_available"] is detail["content_version_known"] is False
        )
        assert len(detail["observations"]) == 1
        # Repeated resolution creates no observations, snapshots, shares or ledger rows.
        await service.resolve_original(**args, object_id=object_id, resolver=resolver)
        assert len((await manager.detail(**args, object_id=object_id))["observations"]) == 1


@pytest.mark.parametrize("user,tenant,connection", [(1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 2)])
async def test_foreign_context_never_reaches_adapter(data, user, tenant, connection):
    with actor(data) as args:
        object_id = await file_id(args)
    resolver = FakeResolver()
    with actor(data, user=user, tenant=tenant, connection=connection) as args:
        with pytest.raises(ConnectionDenied):
            await service.resolve_original(**args, object_id=object_id, resolver=resolver)
    assert resolver.calls == []


@pytest.mark.parametrize("denied", ["list", "read"])
async def test_read_and_list_are_independent_and_both_required(data, denied):
    resolver = FakeResolver()
    with actor(data) as args:
        object_id = await file_id(args)
        await configure(data, restrictions=[{"path": "disk:/work", "deny": [denied]}])
        with pytest.raises(ConnectionDenied):
            await service.resolve_original(**args, object_id=object_id, resolver=resolver)
    assert resolver.calls == []


@pytest.mark.parametrize("kind", ["missing", "directory", "boolean"])
async def test_missing_nonfile_invalid_id_deny_before_adapter(data, kind):
    resolver = FakeResolver()
    with actor(data) as args:
        object_id = await file_id(args, kind="dir" if kind == "directory" else "file")
        if kind == "missing":
            object_id += 10000000
        elif kind == "boolean":
            object_id = True
        with pytest.raises(ConnectionDenied):
            await service.resolve_original(**args, object_id=object_id, resolver=resolver)
    assert not resolver.calls


@pytest.mark.parametrize(
    "change",
    [
        "disabled",
        "owner",
        "membership",
        "user",
        "secret_removed",
        "secret_rotated",
        "account",
        "root",
        "read",
        "list",
        "boundary",
        "metadata",
        "deleted",
    ],
)
async def test_recheck_after_provider_work_rejects_revocation_and_changed_context(data, change):
    async def changed(context):
        # An independent write can finish here: no connection lock over resolver work.
        async with async_session_maker() as session, session.begin():
            if change in {"disabled", "owner", "account"}:
                values = {
                    "disabled": {"is_active": False},
                    "owner": {"owner_user_id": data.users[1].id},
                    "account": {"account_key": "synthetic-other-account"},
                }[change]
                await session.execute(
                    update(WorkspaceConnection).where(WorkspaceConnection.id == context.connection_id).values(**values)
                )
            elif change == "membership":
                await session.execute(
                    update(TenantUser)
                    .where(TenantUser.tenant_id == context.tenant_id, TenantUser.user_id == context.actor_id)
                    .values(is_active=False)
                )
            elif change == "user":
                await session.execute(update(User).where(User.id == context.actor_id).values(is_active=False))
            elif change == "secret_removed":
                await session.execute(
                    delete(WorkspaceConnectionSecret).where(
                        WorkspaceConnectionSecret.connection_id == context.connection_id
                    )
                )
            elif change == "secret_rotated":
                await session.execute(
                    update(WorkspaceConnectionSecret)
                    .where(WorkspaceConnectionSecret.connection_id == context.connection_id)
                    .values(secret_encrypted=encrypt_secret("different-synthetic-token"))
                )
            elif change == "metadata":
                await session.execute(
                    update(FileCatalogObject)
                    .where(FileCatalogObject.id == context.object_id)
                    .values(observation_number=2)
                )
            elif change == "deleted":
                await session.execute(delete(FileCatalogObject).where(FileCatalogObject.id == context.object_id))
        if change == "root":
            await configure(data, root="disk:/elsewhere")
        elif change in {"read", "list"}:
            await configure(data, restrictions=[{"path": "disk:/work", "deny": [change]}])
        elif change == "boundary":
            await configure(data, extra={"boundary": "local_only"})

    resolver = FakeResolver(changed)
    with actor(data) as args:
        object_id = await file_id(args)
        with pytest.raises(ConnectionDenied):
            await asyncio.wait_for(service.resolve_original(**args, object_id=object_id, resolver=resolver), 3)
    assert len(resolver.calls) == 1


@pytest.mark.parametrize(
    "case", ["provider_error", "timeout", "cancellation", "wrong_file", "download_target", "expired"]
)
async def test_failed_provider_resolution_is_sanitized_or_cancellation_propagates(data, monkeypatch, case):
    class Resolver(FakeResolver):
        async def resolve(self, context):
            if case == "provider_error":
                raise RuntimeError("secret source/path/token/URL MUST NOT ESCAPE")
            if case == "timeout":
                await asyncio.sleep(1)
            if case == "cancellation":
                raise asyncio.CancelledError()
            target = await super().resolve(context)
            if case == "wrong_file":
                return replace(target, context=replace(context, object_id=context.object_id + 1))
            if case == "download_target":
                return replace(target, url="https://downloader.dst.yandex.ru/disk/bearer")
            if case == "expired":
                return replace(target, expires_at=context.issued_at)
            return target

    monkeypatch.setattr(service, "RESOLVE_TIMEOUT", 0.02)
    with actor(data) as args:
        object_id = await file_id(args)
        error = asyncio.CancelledError if case == "cancellation" else OriginalOpenUnavailable
        with pytest.raises(error) as caught:
            await service.resolve_original(**args, object_id=object_id, resolver=Resolver())
        if case != "cancellation":
            assert str(caught.value) == "original_unavailable"


async def test_no_default_adapter_or_implicit_capability(data):
    with actor(data) as args:
        object_id = await file_id(args)
        with pytest.raises(OriginalOpenUnavailable):
            await service.resolve_original(**args, object_id=object_id)
        resolver = FakeResolver()
        resolver.link_policy = None
        with pytest.raises(OriginalOpenUnavailable):
            await service.resolve_original(**args, object_id=object_id, resolver=resolver)
        assert not resolver.calls


async def test_context_expiry_and_cross_object_replay_never_authorize(data):
    with actor(data) as args:
        object_id = await file_id(args)
        context = await manager.prepare_original(**args, object_id=object_id)
        for changed in (
            replace(context, issued_at=context.issued_at - OPEN_TTL),
            replace(context, issued_at=context.issued_at + timedelta(seconds=10)),
            replace(context, object_id=object_id + 1),
            replace(context, credential_key="wrong"),
            None,
        ):
            with pytest.raises(ConnectionDenied):
                await manager.check_original(**args, object_id=object_id, context=changed)


@pytest.mark.parametrize("boundary", ["local_only", "company"])
async def test_noncloud_metadata_never_reaches_resolver(data, boundary):
    resolver = FakeResolver()
    with actor(data) as args:
        object_id = await file_id(args)
        await configure(data, extra={"boundary": boundary})
        with pytest.raises(ConnectionDenied):
            await service.resolve_original(**args, object_id=object_id, resolver=resolver)
    assert not resolver.calls


async def test_previous_resolution_target_is_not_reused_for_new_request(data):
    class CachingResolver(FakeResolver):
        cached = None

        async def resolve(self, context):
            if self.cached is None:
                self.cached = await super().resolve(context)
            return self.cached

    with actor(data) as args:
        object_id = await file_id(args)
        resolver = CachingResolver()
        await service.resolve_original(**args, object_id=object_id, resolver=resolver)
        with pytest.raises(OriginalOpenUnavailable):
            await service.resolve_original(**args, object_id=object_id, resolver=resolver)


async def test_postcheck_wait_cannot_release_an_expired_target(data, monkeypatch):
    original = manager.check_original

    async def expired(**kwargs):
        await original(**kwargs)
        # Equivalent to exceeding the context lifetime while waiting for a lock.
        monkeypatch.setattr("app.integrations.file_workspace.original_open.OPEN_TTL", timedelta(0))

    monkeypatch.setattr(FileCatalogManager, "check_original", lambda self, **kwargs: expired(**kwargs))
    with actor(data) as args:
        object_id = await file_id(args)
        with pytest.raises(OriginalOpenUnavailable):
            await service.resolve_original(**args, object_id=object_id, resolver=FakeResolver())
