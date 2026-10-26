"""Existing receipt projection + real ASGI; synthetic owned tenants, no sends."""

import base64
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner

from app.core.config import settings
from app.core.permissions import operator_permission_scope, permission_scope
from app.core.tenant_context import current_tenant_id, tenant_scope
from app.jobs.attempt_budget import ATTEMPT_BUDGET_STOP_PREFIX
from app.jobs.recovery_policy import OUTCOME_UNCONFIRMED_PREFIX
from app.main import create_application
from app.models import AgentTask, DigestRun, Job, Notification, NotificationReceipt, TenantUser, WorkspaceRole
from app.services.notifications import delivery_signal as ds
from tests.shared.fixtures.personal_workspace import personal_world
from tests.shared.web_session import session_cookie

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


@pytest.fixture
async def evidence(personal_world):
    w = personal_world
    now = datetime.now(timezone.utc)
    jobs, runs = [], []
    for index in (0, 1):
        with tenant_scope(w.tenants[index].id), operator_permission_scope():
            task = await AgentTask.objects.create(name=w.tag, job_type="digest", cron_expr="0 0 * * *")
            runs.append(
                await DigestRun.objects.create(
                    period="day",
                    period_start=date.today(),
                    period_end=date.today(),
                    channel="telegram",
                    chat_id="private-chat",
                    content="private-content",
                    error="private-error",
                    status="failed",
                )
            )
            for actor, status, error in (
                (w.users[0].id, "pending", "private-error"),
                (w.users[2].id if index else w.users[1].id, "failed", OUTCOME_UNCONFIRMED_PREFIX + "private-error"),
                (None, "failed", ATTEMPT_BUDGET_STOP_PREFIX + "private-error"),
                (None, "failed", None),
                (None, "done", None),
            ):
                jobs.append(
                    await Job.objects.create(
                        job_type="digest",
                        agent_task_id=task.id,
                        initiated_by_user_id=actor,
                        status=status,
                        error=error,
                        attempts=1,
                        max_attempts=3,
                        run_at=now,
                        payload={"chat_id": "private-chat", "content": "private-content"},
                    )
                )
            await Job.objects.create(
                job_type="digest", status="failed", error="private-old", created_at=now - timedelta(days=8), run_at=now
            )
            await Job.objects.create(job_type="collect", status="failed", error="private-other-kind", run_at=now)
    return SimpleNamespace(world=w, jobs=jobs, runs=runs)


def workspace_session(client):
    return json.loads(base64.b64decode(TimestampSigner(settings.SECRET_KEY).unsign(client.cookies.get("session"))))[
        "app_tenant_id"
    ]


async def test_owner_aggregate_manager_own_jobs_no_broadcast_and_read_only(evidence):
    w = evidence.world
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        before = (await Notification.objects.count(), await NotificationReceipt.objects.count())
        signals = await ds.personal_signals(w.users[0])
        assert current_tenant_id() == w.tenants[0].id
        owner, manager = sorted(signals, key=lambda s: s.workspace_id)
        assert owner.workspace_id == w.tenants[0].id and owner.scope == "workspace"
        assert owner.counts == dict(undelivered=2, retrying=1, quarantined=1, unconfirmed=1)
        # The newer done Job for the same task does not erase unrelated old evidence.
        assert len(owner.jobs) == 4 and len(owner.runs) == 1
        assert manager.scope == "initiated" and manager.counts["retrying"] == 1 and manager.total == 1
        assert manager.runs == [] and [j["id"] for j in manager.jobs] == [evidence.jobs[5].id]
        assert (await Notification.objects.count(), await NotificationReceipt.objects.count()) == before
    with permission_scope(w.users[3]):
        assert await ds.personal_signals(w.users[3]) == []
        assert not await ds.navigation_allowed(w.users[3], w.tenants[0].id)


async def test_viewer_own_receipts_and_admin_workspace_aggregate(evidence):
    w = evidence.world
    with tenant_scope(w.tenants[0].id), operator_permission_scope():
        await TenantUser.objects.create(
            tenant_id=w.tenants[0].id,
            user_id=w.users[3].id,
            channel="web",
            external_user_id=str(w.users[3].id),
            role_id=w.roles["viewer"].id,
            is_active=True,
        )
        own = await Job.objects.create(
            job_type="digest",
            status="failed",
            error="private-error",
            attempts=1,
            run_at=datetime.now(timezone.utc),
            initiated_by_user_id=w.users[3].id,
        )
        admin = await WorkspaceRole.objects.get(codename="admin")
        await TenantUser.objects.update_by_id(w.memberships[2].id, role_id=admin.id)
    with permission_scope(w.users[3]):
        (signal,) = await ds.personal_signals(w.users[3])
        assert signal.scope == "initiated" and signal.total == 1 and signal.runs == []
        assert [j["id"] for j in signal.jobs] == [own.id]
    with permission_scope(w.users[1]):
        (signal,) = await ds.personal_signals(w.users[1])
        assert signal.scope == "workspace" and signal.total == 6


async def test_bell_cross_workspace_post_only_and_fresh_revocation(evidence):
    w = evidence.world
    app = create_application()
    origin, destination = (t.id for t in w.tenants)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver.invalid", follow_redirects=False
    ) as client:
        client.cookies.set("session", session_cookie(w.users[0], origin), domain="testserver.invalid")
        snapshot = await client.get("/app/notifications")
        assert snapshot.status_code == 200, snapshot.text
        items = [i for i in snapshot.json()["notifications"] if i["kind"] == "delivery"]
        assert {i["workspace_id"] for i in items} == {origin, destination}
        assert all(i["read_id"] is None and i["action"] == "open" for i in items)
        assert "private-" not in json.dumps(items)
        url = f"/app/notifications/delivery/{destination}"
        landing = await client.get(url)
        assert landing.status_code == 200 and 'method="post"' in landing.text
        assert workspace_session(client) == origin
        assert (await client.post(url, data={"_csrf": "bad"})).status_code == 403
        assert workspace_session(client) == origin
        csrf = app.state.csrf_manager.generate_token()
        response = await client.post(url, data={"_csrf": csrf, "next": "https://evil.invalid", "tenant_id": origin})
        assert response.status_code == 303 and response.headers["location"] == "/app/digests#delivery"
        assert workspace_session(client) == destination
        assert response.headers["cache-control"] == "no-store"
        with tenant_scope(destination), operator_permission_scope():
            await TenantUser.objects.update_by_id(w.memberships[1].id, is_active=False)
        assert (await client.post(url, data={"_csrf": csrf})).status_code == 404
        response = await client.get("/app/notifications")
        assert not any(
            i["kind"] == "delivery" and i["workspace_id"] == destination for i in response.json()["notifications"]
        )


async def test_foreign_workspace_and_no_auth_fail_closed(evidence):
    w = evidence.world
    app = create_application()
    url = f"/app/notifications/delivery/{w.tenants[1].id}"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver.invalid", follow_redirects=False
    ) as client:
        unauth = await client.get(url)
        assert unauth.status_code == 302 and "/login" in unauth.headers["location"]
        client.cookies.set("session", session_cookie(w.users[1], w.tenants[0].id), domain="testserver.invalid")
        assert (await client.get(url)).status_code == 404
        assert (await client.post(url, data={"_csrf": app.state.csrf_manager.generate_token()})).status_code == 404
        assert workspace_session(client) == w.tenants[0].id


async def test_real_digests_panel_is_scoped_redacted_and_read_only(evidence):
    w = evidence.world
    app = create_application()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver.invalid") as client:
        for index in (0, 1):
            client.cookies.clear()
            client.cookies.set("session", session_cookie(w.users[0], w.tenants[index].id), domain="testserver.invalid")
            response = await client.get("/app/digests")
            assert response.status_code == 200, response.text[:500]
            section = response.text.split('<section id="delivery"', 1)[1].split("</section>", 1)[0]
            assert "data-delivery-bucket" in section and "сейчас недоступна" not in section
            assert "private-" not in section
            if index == 1:
                assert "Только задания" in section and "data-delivery-run" not in section
                assert f'data-delivery-job="{evidence.jobs[5].id}"' in section
                assert f'data-delivery-job="{evidence.jobs[6].id}"' not in section
        with tenant_scope(w.tenants[1].id), permission_scope(w.users[0]):
            row = await Job.objects.get(id=evidence.jobs[5].id)
            assert row.status == "pending" and row.attempts == 1 and row.error == "private-error"


async def test_delivery_read_failure_is_not_reported_as_empty(evidence, monkeypatch):
    w = evidence.world
    app = create_application()
    monkeypatch.setattr(
        ds.DeliverySignalRepository, "jobs", AsyncMock(side_effect=RuntimeError("synthetic unavailable"))
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver.invalid") as client:
        client.cookies.set("session", session_cookie(w.users[0], w.tenants[0].id), domain="testserver.invalid")
        snapshot = (await client.get("/app/notifications")).json()
        assert "deliveries" in snapshot["unavailable"] and not snapshot["complete"]
        assert not [i for i in snapshot["notifications"] if i["kind"] == "delivery"]
        response = await client.get("/app/digests")
        section = response.text.split('<section id="delivery"', 1)[1].split("</section>", 1)[0]
        assert "Сводка доставки сейчас недоступна" in section
        assert "синтет" not in section and "synthetic unavailable" not in section
