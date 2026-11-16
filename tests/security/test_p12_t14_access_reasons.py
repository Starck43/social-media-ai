"""Personal-source failure reasons and protected notification destinations."""

from types import SimpleNamespace

import pytest

from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import TenantUser
from app.models.managers.tenant_manager import TenantUserManager
from app.services.notifications.inbox import PersonalInbox
from app.services.notifications.service import notify
from app.services.social import source_access
from app.services.social.connections import ConnectionStatus
from app.types import NotificationType
from tests.collection.test_p12_t16_source_access import _source, make_user, workspace  # noqa: F401
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401


def _operator_status():
    return ConnectionStatus("vk", "reauth", "unreadable", "contact operator", True, False, operator_attention=True)


async def test_preflight_distinguishes_operator_from_personal_action(workspace, make_user, monkeypatch):
    first, second = await make_user(), await make_user()
    for user in (first, second):
        await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=user.id, role="member")
    sources = [
        _source(workspace.id, source_id=1, owner_user_id=first.id),
        _source(workspace.id, source_id=2, owner_user_id=second.id),
    ]

    async def statuses(rows, *, owner_overrides=None):
        return {1: _operator_status(), 2: ConnectionStatus("vk", "missing", "missing", "", True)}

    monkeypatch.setattr(source_access, "source_connection_statuses", statuses)
    report = (await source_access.preflight(sources))[0]
    assert report.blocked_source_ids == (1, 2) and report.operator_source_ids == (1,)
    assert "администратору" in report.summary() and "авторизуйтесь" in report.summary()
    assert report.destination == "/app/settings?tab=connections"
    reports = await source_access.authorisation_reports(sources, required_source_ids={1, 2})
    assert len(reports) == 2
    operator = next(r for r in reports if r.owner_user_id == first.id)
    personal = next(r for r in reports if r.owner_user_id == second.id)
    assert operator.operator_count == 1 and "администратору" in operator.message
    assert "Подключите аккаунт" not in operator.message
    assert operator.destination == "/app/settings?tab=connections"
    assert operator.dedup_key != personal.dedup_key
    assert personal.operator_count == 0 and "Подключите аккаунт" in personal.message


async def test_reason_and_count_bound_dedup_content():
    a = source_access.AuthRequiredReport("vk", 10, 1, 1)
    b = source_access.AuthRequiredReport("vk", 10, 2, 2)
    mixed = source_access.AuthRequiredReport("vk", 10, 2, 1)
    assert len({a.dedup_key, b.dedup_key, mixed.dedup_key}) == 3
    assert "администратору" in mixed.message and "личная авторизация" in mixed.message


@pytest.mark.tenancy
async def test_personal_notice_fixed_route_rechecks_membership(personal_world):
    w = personal_world
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        notice = await notify.create(
            "Источник недоступен",
            "Обратитесь к администратору",
            NotificationType.SOURCE_AUTH_REQUIRED,
            entity_type="connection",
            entity_id=None,
            recipient_user_ids=[w.users[0].id],
            severity="action_required",
        )
        other_type = await notify.create(
            "Other",
            "synthetic",
            NotificationType.API_ERROR,
            entity_type="connection",
            entity_id=None,
            recipient_user_ids=[w.users[0].id],
        )
        entries = await PersonalInbox(w.users[0]).entries()
        entry = next(e for e in entries if e.event.id == notice.id)
        assert entry.destination == "/app/settings?tab=connections"
        assert not any(e.event.id == other_type.id for e in entries)
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[1]):
        assert not any(e.event.id == notice.id for e in await PersonalInbox(w.users[1]).entries())
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        membership = next(m for m in w.memberships if m.tenant_id == w.tenants[0].id and m.user_id == w.users[0].id)
        await TenantUser.objects.update_by_id(membership.id, is_active=False)
        try:
            assert not any(e.event.id == notice.id for e in await PersonalInbox(w.users[0]).entries())
        finally:
            await TenantUser.objects.update_by_id(membership.id, is_active=True)
