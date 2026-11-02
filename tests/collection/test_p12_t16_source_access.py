"""P12-T16: personal source ownership, service-only collection, auth notices.

Covers the four rules the owner chose:

1. a source collects under the member who added it, never the workspace owner;
2. a source with no personal credential collects through the deployment
   credential only (L1) and is labelled as such;
3. the preflight before a run names blocking platforms only (missing/reauth)
   and leaves `renewable` alone;
4. a run that hit `auth_required` publishes one personal, deduplicated notice
   per owner and platform with a working authorisation link.

Runs against the real test schema; the notification assertions use the real
publish path (no external delivery) and are addressed to synthetic users.
"""

from __future__ import annotations

import secrets
from types import SimpleNamespace

import pytest

from app.models import Role, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.services.social import source_access as access_module
from app.services.social.connections import (
    ConnectionStatus,
    blocks_collection,
    is_service_only,
    source_uses_personal_token,
)


def _source(tenant_id, *, source_id=1, platform="vk", mode="pull", owner_user_id=None, token_owner=None):
    params = {"mode": mode} if mode is not None else {}
    if token_owner is not None:
        params["token_owner"] = token_owner
    return SimpleNamespace(
        id=source_id,
        name=f"source-{source_id}",
        tenant_id=tenant_id,
        params=params,
        owner_user_id=owner_user_id,
        platform=SimpleNamespace(platform_type="vk" if platform == "vk" else platform),
    )


@pytest.fixture
async def workspace():
    tenant = await tenants.create(name="P12-T16", slug=f"p12t16{secrets.token_hex(4)}")
    yield tenant
    await tenants.delete_by_id(tenant.id)


@pytest.fixture
async def make_user():
    created = []
    role = await Role.objects.get(codename="user")

    async def _make():
        username = f"p12t16{secrets.token_hex(4)}"
        user = await User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="secret-password-1",
            role_id=role.id,
        )
        created.append(user)
        return user

    yield _make
    for user in created:
        await User.objects.delete_by_id(user.id)


def test_auto_absent_mode_and_pull_consult_personal_access():
    """Imported lanes store `auto`; treating it as "no personal access" hid them."""
    assert source_uses_personal_token(_source(1, mode="auto"))
    assert source_uses_personal_token(_source(1, mode="pull"))
    assert source_uses_personal_token(_source(1, mode="user"))
    assert source_uses_personal_token(_source(1, mode=None))


def test_push_never_asks_for_a_personal_secret():
    assert not source_uses_personal_token(_source(1, mode="push"))


def _status(state: str) -> ConnectionStatus:
    return ConnectionStatus(platform="vk", state=state, label=state, detail="", needs_action=state in ("missing", "reauth"))


def test_only_missing_and_reauth_block_collection():
    assert blocks_collection(_status("missing"))
    assert blocks_collection(_status("reauth"))
    assert not blocks_collection(_status("renewable"))
    assert not blocks_collection(_status("connected"))
    assert not blocks_collection(_status("unconfigured"))


def test_service_only_is_its_own_state():
    assert is_service_only(_status("unconfigured"))
    assert not is_service_only(_status("missing"))
    assert not is_service_only(None)


async def test_owner_is_the_member_who_added_the_source(workspace, make_user):
    """A non-owner member's source never collects with the workspace owner's token."""
    owner = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="owner")
    member = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=member.id, role="member")

    source = _source(workspace.id, owner_user_id=member.id)
    resolved = await access_module.resolve_access([source])
    assert resolved[source.id].owner_user_id == member.id


async def test_ownerless_source_has_no_owner_despite_a_workspace_owner(workspace, make_user):
    owner = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="owner")

    source = _source(workspace.id)
    resolved = await access_module.resolve_access([source])
    assert resolved[source.id].owner_user_id is None


async def test_preflight_reports_blocking_and_service_only_platforms(workspace, make_user, monkeypatch):
    owner = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="member")
    blocked = _source(workspace.id, source_id=1, owner_user_id=owner.id)
    legacy = _source(workspace.id, source_id=2)  # no personal owner at all
    push = _source(workspace.id, source_id=3, mode="push", owner_user_id=owner.id)

    async def fake_statuses(sources, *, owner_overrides=None):
        by_id = {getattr(row, "id", None): row for row in sources}
        result = {}
        for source_id in by_id:
            if source_id == 1:
                result[source_id] = _status("missing")
            elif source_id == 2:
                result[source_id] = _status("unconfigured")
        return result

    monkeypatch.setattr(access_module, "source_connection_statuses", fake_statuses)

    reports = await access_module.preflight([blocked, legacy, push])
    assert [report.platform for report in reports] == ["vk"]
    report = reports[0]
    assert report.blocked_source_ids == (1,)
    assert report.service_only_source_ids == (2,)
    assert report.destination == "/app/connections/vk/authorize?next=/app/sources"
    assert 3 not in report.owners_by_source


async def test_renewable_is_not_a_preflight_problem(workspace, make_user, monkeypatch):
    owner = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="member")
    source = _source(workspace.id, source_id=1, owner_user_id=owner.id)

    async def fake_statuses(sources, *, owner_overrides=None):
        return {getattr(row, "id", None): _status("renewable") for row in sources}

    monkeypatch.setattr(access_module, "source_connection_statuses", fake_statuses)
    monkeypatch.setattr(access_module, "blocks_collection", blocks_collection)
    assert await access_module.preflight([source]) == []


async def test_authorisation_report_names_platform_count_and_owner(workspace, make_user, monkeypatch):
    owner = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="member")
    sources = [
        _source(workspace.id, source_id=1, owner_user_id=owner.id),
        _source(workspace.id, source_id=2, owner_user_id=owner.id),
        _source(workspace.id, source_id=3),  # no owner: nothing personal to ask for
    ]

    async def fake_statuses(rows, *, owner_overrides=None):
        return {getattr(row, "id", None): _status("missing") for row in rows}

    monkeypatch.setattr(access_module, "source_connection_statuses", fake_statuses)

    reports = await access_module.authorisation_reports(sources, required_source_ids={1, 2, 3})
    assert len(reports) == 1
    report = reports[0]
    assert report.platform == "vk"
    assert report.owner_user_id == owner.id
    assert report.source_count == 2
    assert "2" in report.message and "ВКонтакте" in report.message
    assert report.destination.endswith("/authorize?next=/app/sources")
    assert report.dedup_key == f"source-auth-required:vk:{owner.id}"


async def test_authorisation_notice_is_personal_and_deduplicated(workspace, make_user):
    owner = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="member")
    outsider = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=outsider.id, role="member")
    report = access_module.AuthRequiredReport(platform="vk", owner_user_id=owner.id, source_count=2)

    from app.services.notifications.service import notify
    from app.types import NotificationType

    created = []

    original = notify.create

    async def record(*args, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(id=len(created), tenant_id=workspace.id, title=kwargs.get("title") or (args[0] if args else ""))

    from app.services.notifications import service as service_module

    service_module.notify.create = record
    try:
        sent = await access_module.notify_authorisation_required([report])
    finally:
        service_module.notify.create = original

    assert sent == 1
    assert len(created) == 1
    kwargs = created[0]
    assert kwargs["recipient_user_ids"] == [owner.id]
    assert kwargs["ntype"] is NotificationType.SOURCE_AUTH_REQUIRED
    assert kwargs["severity"] == "action_required"
    assert kwargs["dedup_key"] == f"source-auth-required:vk:{owner.id}"
    assert outsider.id not in kwargs["recipient_user_ids"]
    assert "token" not in str(kwargs).lower()
