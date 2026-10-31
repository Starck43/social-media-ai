"""Serialized PostgreSQL/HTTP/Safari acceptance for workspace offboarding.

This module owns uniquely named synthetic records in the root conftest's local
``test_schema``. It creates the real app without lifespan/background workers.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session as SyncSession

_LIVE_SERVER_PORTS: set[int] = set()
_EXPECTED_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"
_CONFTEST = next(
    (module for module in tuple(sys.modules.values()) if getattr(module, "__file__", None) == str(_EXPECTED_CONFTEST)),
    None,
)
if _CONFTEST is None or getattr(_CONFTEST, "TEST_SCHEMA", None) != "test_schema":
    raise RuntimeError("STOP: root conftest must redirect to test_schema; --noconftest is forbidden")
_TARGET = urlsplit(getattr(_CONFTEST, "TEST_DATABASE_URL", ""))
if (
    _TARGET.hostname not in {"localhost", "127.0.0.1"}
    or (_TARGET.port or 5432) != 5432
    or _TARGET.path != "/social_manager"
):
    raise RuntimeError("STOP: only localhost:5432/social_manager/test_schema is approved")

from app.admin.csrf import SESSION_ID_KEY
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import operator_permission_scope, permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import (
    ActionAuditEvent,
    LLMBudgetDay,
    Permission,
    PlatformRole,
    Tenant,
    User,
    WorkspaceOffboardingEvent,
)
from app.services.tenancy import offboarding

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
PASSWORD = "Synthetic-P11-T29-Offboard!"
CSRF = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"')


@contextmanager
def platform_scope():
    """Trusted synthetic setup/cleanup only; never wraps an ASGI request."""
    with tenant_scope(bypass=True), operator_permission_scope():
        yield


@contextmanager
def actor_scope(actor):
    with tenant_scope(bypass=True), permission_scope(actor):
        yield


@asynccontextmanager
async def admin_client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://testserver",
        follow_redirects=False,
    ) as client:
        yield client


def session_id(app, client):
    from starlette.middleware.sessions import SessionMiddleware

    signer = SessionMiddleware(app, secret_key=settings.SECRET_KEY).signer
    raw = base64.b64decode(signer.unsign(client.cookies["session"].encode()))
    return json.loads(raw)[SESSION_ID_KEY]


def csrf_token(html):
    match = CSRF.search(html)
    assert match, "Offboarding form must contain a CSRF token"
    return match.group(1)


async def login(client, user):
    page = await client.get("/admin/login")
    assert page.status_code == 200, page.text
    response = await client.post(
        "/admin/login",
        data={"username": user.username, "password": PASSWORD, "csrf_token": csrf_token(page.text)},
    )
    assert response.status_code in {302, 303}, f"Real console login failed: {response.status_code}: {response.text}"


async def record_expired_audit(tenant_id: int, actor_id: int, resource_id: int) -> int:
    """Use the audit manager, setting historical time before its INSERT flush."""

    def backdate_new_event(session, flush_context, instances):
        for instance in session.new:
            if isinstance(instance, ActionAuditEvent) and instance.resource_id == str(resource_id):
                instance.occurred_at = datetime.now(timezone.utc) - timedelta(days=8)

    sqlalchemy_event.listen(SyncSession, "before_flush", backdate_new_event)
    try:
        with tenant_scope(tenant_id):
            async with async_session_maker() as session:
                audit = await ActionAuditEvent.objects.record_success(
                    session=session,
                    actor_kind="user",
                    actor_id=actor_id,
                    action="scheduling.task_admit",
                    resource_kind="agent_task",
                    resource_id=resource_id,
                )
                await session.commit()
                return audit.id
    finally:
        sqlalchemy_event.remove(SyncSession, "before_flush", backdate_new_event)


@pytest.fixture
def live_offboarding_app(monkeypatch):
    """Create the real app without lifespan/runtime, confined to loopback DB."""
    monkeypatch.setattr(settings, "ADMIN_ENABLED", True)
    forbidden = []
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def guard(original):
        def checked(sock, address):
            if sock.family in {socket.AF_INET, socket.AF_INET6}:
                loopback = address[0] in {"localhost", "127.0.0.1", "::1"}
                if not loopback or (address[1] != 5432 and address[1] not in _LIVE_SERVER_PORTS):
                    forbidden.append(f"{address[0]}:{address[1]}")
                    raise AssertionError("Only approved loopback PostgreSQL and owned Safari server are allowed")
            return original(sock, address)

        return checked

    monkeypatch.setattr(socket.socket, "connect", guard(original_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(original_connect_ex))
    yield create_application()
    assert not forbidden, f"Forbidden network attempts: {forbidden}"


@pytest.fixture
async def live_records():
    """Own synthetic identities/workspaces and restore canonical admin grants."""
    marker = uuid4().hex[:12]
    users: list[int] = []
    tenants: list[int] = []
    budget_day_ids: list[int] = []
    original_admin_grants = None
    admin_role_id = None
    try:
        with platform_scope():
            admin_role = await PlatformRole.objects.get_by_codename("admin")
            owner_role = await PlatformRole.objects.get_by_codename("superuser")
            assert admin_role is not None and owner_role is not None, "Expected seeded platform roles"
            admin_role_id = admin_role.id
            loaded_admin = await PlatformRole.objects.get_with_permissions(admin_role.id)
            original_admin_grants = sorted(permission.id for permission in loaded_admin.permissions)
            # Even the broadest ordinary role cannot acquire reserved offboarding
            # authority from a stale/full catalog grant set.
            all_permissions = await Permission.objects.all()
            await PlatformRole.objects.set_permissions(admin_role.id, [item.id for item in all_permissions])

            admin = await User.objects.create_user(
                username=f"p11t29-admin-{marker}",
                email=f"p11t29-admin-{marker}@example.invalid",
                password=PASSWORD,
                role_id=admin_role.id,
                is_active=True,
            )
            owner = await User.objects.create_user(
                username=f"p11t29-owner-{marker}",
                email=f"p11t29-owner-{marker}@example.invalid",
                password=PASSWORD,
                role_id=owner_role.id,
                is_active=True,
            )
            admin = await User.objects.prefetch_related("role").get(id=admin.id)
            owner = await User.objects.prefetch_related("role").get(id=owner.id)
            users.extend([admin.id, owner.id])
            targets = {}
            for key, label in (
                ("owner", "confirmed delete"),
                ("admin", "admin denial"),
                ("retained", "retained audit"),
                ("payer", "payer restriction"),
                ("concurrent", "concurrent replay"),
                ("competing", "competing reviews"),
                ("stale", "stale review"),
                ("rollback", "rollback race"),
                ("expired", "elapsed audit"),
                ("revoked", "revoked owner"),
            ):
                workspace = await Tenant.objects.create(
                    name=f"P11-T29 {label} {marker}", slug=f"p11-t29-{key}-{marker}", plan="starter"
                )
                targets[key] = workspace.id
                tenants.append(workspace.id)
        retained_event = None
        with tenant_scope(targets["retained"]):
            async with async_session_maker() as session:
                retained_event = await ActionAuditEvent.objects.record_success(
                    session=session,
                    actor_kind="user",
                    actor_id=owner.id,
                    action="scheduling.task_admit",
                    resource_kind="agent_task",
                    resource_id=1,
                )
                await session.commit()
        yield {
            "admin": admin,
            "owner": owner,
            "targets": targets,
            "audit_event_id": retained_event.id,
            "budget_day_ids": budget_day_ids,
        }
    finally:
        with platform_scope():
            if original_admin_grants is not None and admin_role_id is not None:
                await PlatformRole.objects.set_permissions(admin_role_id, original_admin_grants)
            for budget_day_id in budget_day_ids:
                async with async_session_maker() as session:
                    await LLMBudgetDay.objects.delete_by_id(budget_day_id, session=session)
                    await session.commit()
            # The retained fixture event is removed only through the existing
            # plan-aligned retention manager; ordinary deletes remain refused.
            for tenant_id in tenants:
                if await Tenant.objects.get(id=tenant_id) is not None:
                    with tenant_scope(tenant_id):
                        async with async_session_maker() as session:
                            await ActionAuditEvent.objects.delete_expired(
                                now=datetime.now(timezone.utc) + timedelta(days=100),
                                batch_size=5000,
                                session=session,
                            )
                            await session.commit()
                    await Tenant.objects.delete_by_id(tenant_id)
            for user_id in users:
                user = await User.objects.prefetch_related("role").get(id=user_id)
                if user is not None:
                    if user.is_superuser:
                        assert admin_role_id is not None
                        await User.objects.update_by_id(user_id, role_id=admin_role_id)
                    await User.objects.delete_user(user_id)


async def test_admin_and_legacy_delete_cannot_bypass_confirmation(live_offboarding_app, live_records):
    target = live_records["targets"]["admin"]
    async with admin_client(live_offboarding_app) as client:
        await login(client, live_records["admin"])
        token = live_offboarding_app.state.csrf_manager.generate_token(
            session_id=session_id(live_offboarding_app, client)
        )
        admin_delete = await client.delete(
            "/admin/tenant/delete", params={"pks": target}, headers={"X-CSRF-Token": token}
        )
        admin_action = await client.get(f"/admin/tenant/action/offboard?pks={target}")
    assert admin_delete.status_code == 403
    assert admin_action.status_code == 403
    with platform_scope():
        assert await Tenant.objects.get(id=target) is not None

    async with admin_client(live_offboarding_app) as client:
        await login(client, live_records["owner"])
        token = live_offboarding_app.state.csrf_manager.generate_token(
            session_id=session_id(live_offboarding_app, client)
        )
        owner_legacy_delete = await client.delete(
            "/admin/tenant/delete", params={"pks": target}, headers={"X-CSRF-Token": token}
        )
        missing_csrf = await client.post(
            "/admin/tenant/action/offboard",
            data={"intent": "preview", "pks": target, "reason_code": "owner_request", "retention_policy": "preserve"},
        )
    assert owner_legacy_delete.status_code in {403, 409}
    assert missing_csrf.status_code == 403
    with platform_scope():
        assert await Tenant.objects.get(id=target) is not None


async def test_superuser_confirm_is_idempotent_and_persists_journal(live_records):
    owner = live_records["owner"]
    workspace_id = live_records["targets"]["owner"]
    with actor_scope(owner):
        preview = await offboarding.preview_offboarding(owner.id, workspace_id)
        assert preview["can_delete"] is True
        assert preview["retained_audit_count"] == preview["expired_audit_count"] == 0
        receipt = await offboarding.confirm_offboarding(owner.id, preview["review_token"])
        replay = await offboarding.confirm_offboarding(owner.id, preview["review_token"])
    assert receipt == replay
    assert receipt["outcome"] == "succeeded"
    assert receipt["workspace_id"] == workspace_id
    with platform_scope():
        assert await Tenant.objects.get(id=workspace_id) is None
    with actor_scope(owner):
        async with async_session_maker() as session:
            rows = await WorkspaceOffboardingEvent.objects.list_events(session=session, limit=100)
    matching = [row for row in rows if row.event_id == receipt["event_id"]]
    assert len(matching) == 1
    assert matching[0].actor_id == owner.id
    assert matching[0].workspace_id == workspace_id
    assert not WorkspaceOffboardingEvent.__table__.foreign_keys
    with actor_scope(owner):
        async with async_session_maker() as session:
            await WorkspaceOffboardingEvent.objects.ensure_available(session=session)
    for mutation in (
        "UPDATE test_schema.workspace_offboarding_events SET reason_code = 'duplicate' "
        f"WHERE event_id = '{receipt['event_id']}'",
        "DELETE FROM test_schema.workspace_offboarding_events " f"WHERE event_id = '{receipt['event_id']}'",
        "TRUNCATE test_schema.workspace_offboarding_events",
    ):
        async with async_session_maker() as session:
            with pytest.raises(DBAPIError):
                await session.execute(text(mutation))
            await session.rollback()
    with actor_scope(owner):
        async with async_session_maker() as session:
            rows = await WorkspaceOffboardingEvent.objects.list_events(session=session, limit=100)
    assert sum(row.event_id == receipt["event_id"] for row in rows) == 1


async def test_concurrent_same_review_replays_once(live_records):
    owner = live_records["owner"]
    workspace_id = live_records["targets"]["concurrent"]
    with actor_scope(owner):
        preview = await offboarding.preview_offboarding(owner.id, workspace_id)
        first, second = await asyncio.gather(
            offboarding.confirm_offboarding(owner.id, preview["review_token"]),
            offboarding.confirm_offboarding(owner.id, preview["review_token"]),
        )
    assert first == second
    with actor_scope(owner):
        async with async_session_maker() as session:
            rows = await WorkspaceOffboardingEvent.objects.list_events(session=session, limit=100)
    assert sum(row.event_id == first["event_id"] for row in rows) == 1


async def test_competing_reviews_allow_only_one_success(live_records):
    owner = live_records["owner"]
    workspace_id = live_records["targets"]["competing"]
    with actor_scope(owner):
        one = await offboarding.preview_offboarding(owner.id, workspace_id)
        two = await offboarding.preview_offboarding(owner.id, workspace_id)
        results = await asyncio.gather(
            offboarding.confirm_offboarding(owner.id, one["review_token"]),
            offboarding.confirm_offboarding(owner.id, two["review_token"]),
            return_exceptions=True,
        )
    receipts = [item for item in results if isinstance(item, dict)]
    failures = [item for item in results if isinstance(item, offboarding.OffboardingError)]
    assert len(receipts) == len(failures) == 1
    assert failures[0].status_code == 409
    with actor_scope(owner):
        async with async_session_maker() as session:
            rows = await WorkspaceOffboardingEvent.objects.list_events(session=session, limit=100)
    assert sum(row.workspace_id == workspace_id for row in rows) == 1


async def test_retained_audit_always_blocks_and_survives(live_records):
    owner = live_records["owner"]
    workspace_id = live_records["targets"]["retained"]
    with actor_scope(owner):
        preview = await offboarding.preview_offboarding(owner.id, workspace_id, retention_policy="expire_elapsed")
        assert preview["retained_audit_count"] == 1
        assert preview["expired_audit_count"] == 0
        assert preview["can_delete"] is False
        with pytest.raises(offboarding.OffboardingError) as error:
            await offboarding.confirm_offboarding(owner.id, preview["review_token"])
    assert error.value.status_code == 409
    assert error.value.code == "retention_blocked"
    with platform_scope():
        assert await Tenant.objects.get(id=workspace_id) is not None
    with tenant_scope(workspace_id):
        event = await ActionAuditEvent.objects.get(id=live_records["audit_event_id"])
    assert event is not None


async def test_financial_budget_restrict_fk_is_preview_blocker_and_persists(live_records):
    owner = live_records["owner"]
    workspace_id = live_records["targets"]["payer"]
    from datetime import date

    with tenant_scope(workspace_id):
        async with async_session_maker() as session:
            budget = await LLMBudgetDay.objects.create(
                session=session,
                tenant_id=workspace_id,
                utc_day=date.today(),
                settled_usd=0,
                reserved_usd=0,
                undefined_count=0,
                unbounded_count=0,
                frozen=True,
                baseline_version="p11-t29-test",
            )
            await session.commit()
    live_records["budget_day_ids"].append(budget.id)
    with actor_scope(owner):
        preview = await offboarding.preview_offboarding(owner.id, workspace_id)
        assert preview["protected_record_count"] >= 1
        assert preview["can_delete"] is False
        with pytest.raises(offboarding.OffboardingError) as error:
            await offboarding.confirm_offboarding(owner.id, preview["review_token"])
    assert (error.value.status_code, error.value.code) == (409, "retention_blocked")
    with platform_scope():
        assert await Tenant.objects.get(id=workspace_id) is not None
    async with async_session_maker() as session:
        assert await LLMBudgetDay.objects.get(id=budget.id, session=session) is not None


async def test_expire_elapsed_requires_explicit_policy_and_records_exact_count(live_records):
    owner = live_records["owner"]
    workspace_id = live_records["targets"]["expired"]
    audit_id = await record_expired_audit(workspace_id, owner.id, 910001)
    with actor_scope(owner):
        preserve = await offboarding.preview_offboarding(owner.id, workspace_id, retention_policy="preserve")
        assert preserve["retained_audit_count"] == 0
        assert preserve["expired_audit_count"] == 1
        assert preserve["can_delete"] is False
        reviewed = await offboarding.preview_offboarding(owner.id, workspace_id, retention_policy="expire_elapsed")
        assert reviewed["can_delete"] is True
        receipt = await offboarding.confirm_offboarding(owner.id, reviewed["review_token"])
    assert receipt["expired_audit_count"] == 1
    assert receipt["retention_policy"] == "expire_elapsed"
    with platform_scope():
        assert await Tenant.objects.get(id=workspace_id) is None
    async with async_session_maker() as session:
        audit = (
            await session.execute(select(ActionAuditEvent).where(ActionAuditEvent.id == audit_id))
        ).scalar_one_or_none()
    assert audit is None


async def test_stale_review_is_rejected_without_journal(live_records):
    owner = live_records["owner"]
    workspace_id = live_records["targets"]["stale"]
    with actor_scope(owner):
        preview = await offboarding.preview_offboarding(owner.id, workspace_id)
    with platform_scope():
        await Tenant.objects.update_by_id(workspace_id, name="P11-T29 changed after review")
    with actor_scope(owner):
        with pytest.raises(offboarding.OffboardingError) as error:
            await offboarding.confirm_offboarding(owner.id, preview["review_token"])
    assert (error.value.status_code, error.value.code) == (409, "stale_review")
    with platform_scope():
        assert await Tenant.objects.get(id=workspace_id) is not None
    with actor_scope(owner):
        async with async_session_maker() as session:
            rows = await WorkspaceOffboardingEvent.objects.list_events(session=session, limit=100)
    assert not any(row.workspace_id == workspace_id for row in rows)


async def test_revoked_superuser_role_is_rechecked_in_confirmation(live_records):
    owner = live_records["owner"]
    workspace_id = live_records["targets"]["revoked"]
    with actor_scope(owner):
        preview = await offboarding.preview_offboarding(owner.id, workspace_id)
    with platform_scope():
        admin_role = await PlatformRole.objects.get_by_codename("admin")
        await User.objects.update_by_id(owner.id, role_id=admin_role.id)
    with actor_scope(owner):
        with pytest.raises(offboarding.OffboardingError) as error:
            await offboarding.confirm_offboarding(owner.id, preview["review_token"])
    assert (error.value.status_code, error.value.code) == (403, "superuser_required")
    with platform_scope():
        assert await Tenant.objects.get(id=workspace_id) is not None
    with actor_scope(owner):
        async with async_session_maker() as session:
            rows = await WorkspaceOffboardingEvent.objects.list_events(session=session, limit=100)
    assert not any(row.workspace_id == workspace_id for row in rows)


async def test_confirmation_failure_rolls_back_success_journal(live_records, monkeypatch):
    owner = live_records["owner"]
    workspace_id = live_records["targets"]["rollback"]
    audit_id = await record_expired_audit(workspace_id, owner.id, 910002)
    with actor_scope(owner):
        preview = await offboarding.preview_offboarding(owner.id, workspace_id, retention_policy="expire_elapsed")
    original_record = WorkspaceOffboardingEvent.objects.record_success

    async def append_then_add_restricting_payer(**kwargs):
        event = await original_record(**kwargs)
        from datetime import date

        with tenant_scope(workspace_id):
            await LLMBudgetDay.objects.create(
                session=kwargs["session"],
                tenant_id=workspace_id,
                utc_day=date.today(),
                settled_usd=0,
                reserved_usd=0,
                undefined_count=0,
                unbounded_count=0,
                frozen=True,
                baseline_version="p11-t29-race",
            )
        return event

    monkeypatch.setattr(WorkspaceOffboardingEvent.objects, "record_success", append_then_add_restricting_payer)
    with actor_scope(owner):
        with pytest.raises(offboarding.OffboardingError) as error:
            await offboarding.confirm_offboarding(owner.id, preview["review_token"])
    assert (error.value.status_code, error.value.code) == (409, "protected_records")
    with platform_scope():
        assert await Tenant.objects.get(id=workspace_id) is not None
    with actor_scope(owner):
        async with async_session_maker() as session:
            rows = await WorkspaceOffboardingEvent.objects.list_events(session=session, limit=100)
    assert not any(row.workspace_id == workspace_id for row in rows)
    async with async_session_maker() as session:
        audit = (
            await session.execute(select(ActionAuditEvent).where(ActionAuditEvent.id == audit_id))
        ).scalar_one_or_none()
        payer_budget = (
            await session.execute(
                select(LLMBudgetDay).where(
                    LLMBudgetDay.tenant_id == workspace_id,
                    LLMBudgetDay.baseline_version == "p11-t29-race",
                )
            )
        ).scalar_one_or_none()
    assert audit is not None, "Failed workspace deletion must roll back explicit audit expiry"
    assert payer_budget is None, "The synthetic payer-budget blocker belongs to the failed transaction"


async def test_journal_http_view_export_and_real_safari_confirmation(live_offboarding_app, live_records):
    owner = live_records["owner"]
    admin = live_records["admin"]
    owner_target = live_records["targets"]["owner"]
    retained_target = live_records["targets"]["retained"]
    async with admin_client(live_offboarding_app) as client:
        await login(client, admin)
        denied = await client.get("/admin/tenant/offboarding-journal")
        denied_export = await client.get("/admin/tenant/offboarding-journal?format=csv")
    assert denied.status_code == denied_export.status_code == 403

    async with admin_client(live_offboarding_app) as client:
        await login(client, owner)
        setup = await client.get(f"/admin/tenant/action/offboard?pks={owner_target}")
        assert setup.status_code == 200, setup.text
        assert 'name="reason_code"' in setup.text
        assert 'name="retention_policy"' in setup.text
        token = live_offboarding_app.state.csrf_manager.generate_token(
            session_id=session_id(live_offboarding_app, client)
        )
        preview = await client.post(
            "/admin/tenant/action/offboard",
            data={
                "csrf_token": token,
                "intent": "preview",
                "pks": owner_target,
                "reason_code": "owner_request",
                "retention_policy": "preserve",
            },
        )
        assert preview.status_code == 200, preview.text
        assert 'name="confirmed"' in preview.text
        assert 'name="review_token"' in preview.text
        from html import unescape

        token_match = re.search(r'name="review_token" value="([^"]+)"', preview.text)
        assert token_match, preview.text
        review_token = unescape(token_match.group(1))
        no_confirmation = await client.post(
            "/admin/tenant/action/offboard",
            data={"csrf_token": token, "intent": "confirm", "review_token": review_token},
        )
        assert no_confirmation.status_code == 400
        blocked = await client.get(f"/admin/tenant/action/offboard?pks={retained_target}")
        assert blocked.status_code == 200 and "срок хранения" in blocked.text.lower()

    # Real Safari exercises the rendered form, cookies, CSRF and the successful
    # and retention-blocked browser outcomes over an owned loopback server.
    import runpy

    import uvicorn

    helper = runpy.run_path(str(Path(__file__).resolve().parents[1] / "browser/p11_t29_offboarding_safari.py"))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    _LIVE_SERVER_PORTS.add(port)
    server = uvicorn.Server(
        uvicorn.Config(live_offboarding_app, host="127.0.0.1", port=port, lifespan="off", log_level="critical")
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "The loopback test ASGI server did not start"
        helper["run_safari_offboarding"](
            f"http://127.0.0.1:{port}", owner.username, PASSWORD, owner_target, protected=False
        )
        helper["run_safari_offboarding"](
            f"http://127.0.0.1:{port}", owner.username, PASSWORD, retained_target, protected=True
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        _LIVE_SERVER_PORTS.discard(port)
    assert not thread.is_alive(), "Owned loopback ASGI server must stop after Safari walkthrough"

    with platform_scope():
        assert await Tenant.objects.get(id=owner_target) is None
        assert await Tenant.objects.get(id=retained_target) is not None
    async with admin_client(live_offboarding_app) as client:
        await login(client, owner)
        journal = await client.get("/admin/tenant/offboarding-journal")
        export = await client.get("/admin/tenant/offboarding-journal?format=csv")
    assert journal.status_code == 200 and str(owner_target) in journal.text
    assert export.status_code == 200 and "text/csv" in export.headers.get("content-type", "")
    assert str(owner_target) in export.text and "event_id" in export.text
