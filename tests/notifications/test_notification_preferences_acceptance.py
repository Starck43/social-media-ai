"""Real ASGI coverage for personal preference persistence on the existing target."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# isort: off
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select, text, update

from tests.web.test_merged_home_acceptance import PASSWORD, existing_target, home, _login

from app.core.config import settings
from app.core.database import async_engine, async_session_maker
from app.models import NotificationPreference, Role, User
from app.main import create_application

# isort: on

pytestmark = pytest.mark.tenancy


@pytest.fixture(scope="module", autouse=True)
async def preference_table_ready(existing_target):
    async with async_engine.connect() as connection:
        async with connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            present = await connection.scalar(
                text("SELECT to_regclass(:table)"),
                {"table": f"{settings.DB_SCHEMA}.{NotificationPreference.__table__.name}"},
            )
            assert present is not None, "Apply reviewed migration 0098 to the test target before this acceptance run"


@pytest.fixture
async def app_client():
    app = create_application()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True
    ) as client:
        yield app, client


@pytest.mark.asyncio
async def test_personal_preferences_work_without_workspace_and_persist(app_client, home):
    app, client = app_client
    users, _, _ = home
    role = await Role.objects.get(codename="user")
    user = await User.objects.create_user(
        username=f"NoWorkspacePref{len(users)}",
        email=f"NoWorkspacePref{len(users)}@example.com",
        password=PASSWORD,
        role_id=role.id,
    )
    users.append(user)
    await _login(client, user.username)
    token = app.state.csrf_manager.generate_token()

    before = await NotificationPreference.objects.get_for_user(user.id)
    assert before is None
    get = await client.get("/app/notifications/preferences")
    assert get.status_code == 200
    assert get.headers["cache-control"] == "no-store"
    assert get.json()["user_id"] == user.id
    assert get.json()["preferences"] == {
        "version": 1,
        "connections": True,
        "tasks": True,
        "popup": False,
        "quiet": False,
        "start": "22:00",
        "end": "08:00",
    }
    assert await NotificationPreference.objects.get_for_user(user.id) is None

    value = {**get.json()["preferences"], "tasks": False, "quiet": True, "start": "21:30", "end": "07:15"}
    saved = await client.post(
        "/app/notifications/preferences",
        headers={"X-App-User": str(user.id)},
        json={"_csrf": token, "preferences": value},
    )
    assert saved.status_code == 200 and saved.json()["preferences"] == value
    assert await NotificationPreference.objects.get_for_user(user.id) == value

    forged = await client.post(
        "/app/notifications/preferences",
        headers={"X-App-User": str(user.id + 1)},
        json={"_csrf": token, "preferences": value},
    )
    assert forged.status_code == 401
    invalid = await client.post(
        "/app/notifications/preferences",
        json={"_csrf": token, "preferences": {**value, "tasks": 1}},
    )
    assert invalid.status_code == 422

    reset = await client.post(
        "/app/notifications/preferences/reset",
        headers={"X-App-User": str(user.id)},
        json={"_csrf": token},
    )
    assert reset.status_code == 200
    assert reset.json()["preferences"]["tasks"] is True
    assert await NotificationPreference.objects.get_for_user(user.id) == reset.json()["preferences"]


@pytest.mark.asyncio
async def test_disable_racing_preference_save_rolls_back(app_client, home):
    app, client = app_client
    users, _, _ = home
    role = await Role.objects.get(codename="user")
    user = await User.objects.create_user(
        username=f"DisablePref{len(users)}",
        email=f"DisablePref{len(users)}@example.com",
        password=PASSWORD,
        role_id=role.id,
    )
    users.append(user)
    await _login(client, user.username)
    token = app.state.csrf_manager.generate_token()
    prefs = {
        "version": 1,
        "connections": False,
        "tasks": False,
        "popup": True,
        "quiet": False,
        "start": "22:00",
        "end": "08:00",
    }

    lock_requested = asyncio.Event()
    loop = asyncio.get_running_loop()

    def observe_lock(connection, cursor, statement, parameters, context, executemany):
        normalized = statement.replace('"', "").lower()
        if "for update" in normalized and f"{settings.DB_SCHEMA.lower()}.users" in normalized:
            loop.call_soon_threadsafe(lock_requested.set)

    async with async_session_maker() as disabling_session:
        await disabling_session.begin()
        await disabling_session.scalar(select(User.id).where(User.id == user.id).with_for_update())
        event.listen(async_engine.sync_engine, "before_cursor_execute", observe_lock)
        save_task = None
        try:
            save_task = asyncio.create_task(
                client.post(
                    "/app/notifications/preferences",
                    headers={"X-App-User": str(user.id)},
                    json={"_csrf": token, "preferences": prefs},
                )
            )
            await asyncio.wait_for(lock_requested.wait(), timeout=10)
            await disabling_session.execute(update(User).where(User.id == user.id).values(is_active=False))
            await disabling_session.commit()
            response = await asyncio.wait_for(save_task, timeout=10)
        finally:
            event.remove(async_engine.sync_engine, "before_cursor_execute", observe_lock)
            if save_task is not None and not save_task.done():
                save_task.cancel()
                await asyncio.gather(save_task, return_exceptions=True)
            if disabling_session.in_transaction():
                await disabling_session.rollback()

    assert response.status_code == 401
    async with async_session_maker() as verify_session:
        assert await verify_session.get(NotificationPreference, user.id) is None
