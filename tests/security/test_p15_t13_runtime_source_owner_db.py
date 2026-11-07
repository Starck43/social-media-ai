"""PostgreSQL regression for a stored Telegram source without a token owner."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from telethon.errors import FloodWaitError

from app.core.config import settings
from app.core.database import async_session_maker
from app.jobs.handlers import _runtime_source
from app.models import Source
from app.services.monitoring import telegram_collection
from app.services.social import telegram_runtime as runtime
from app.services.social.rate_limit import SourceRateLimited
from app.services.social.telegram_grant_access import source_grant_selection
from tests.security.test_p15_t12_telegram_floodwait_db import flood_runtime_data, install_provider  # noqa: F401
from tests.shared.connector_accounts import connector_actor as actor
from tests.shared.connector_accounts import connector_data  # noqa: F401

pytestmark = pytest.mark.tenancy


async def test_persisted_source_view_reaches_guarded_personal_runtime(flood_runtime_data, monkeypatch):
    data = flood_runtime_data
    async with async_session_maker() as session:
        row = (
            await session.execute(
                select(Source).options(selectinload(Source.platform)).where(Source.id == data.source.id)
            )
        ).scalar_one()
        assert "token_owner" not in row.params
        view = _runtime_source(
            row, task_id=None, task_plan_enabled=False, owner_user_id=None, cli_dates=None, force_refresh=False
        )
        selection = source_grant_selection(view)
        assert selection == data.selection
        assert selection.owner_user_id == row.owner_user_id == data.fixture.users[0].id
    state = install_provider(monkeypatch, data, FloodWaitError, "flood")
    with actor(data.fixture), pytest.raises(SourceRateLimited):
        async with runtime._telegram_history_batch(selection, data.claim, view, limit=10):
            pytest.fail("partial history must not be admitted")
    assert state == {"yielded": 1, "sent": 1, "disconnected": True}


async def test_stored_source_enters_personal_collection(flood_runtime_data, monkeypatch):
    data = flood_runtime_data
    async with async_session_maker() as session:
        row = (
            await session.execute(
                select(Source).options(selectinload(Source.platform)).where(Source.id == data.source.id)
            )
        ).scalar_one()
        view = _runtime_source(
            row, task_id=None, task_plan_enabled=False, owner_user_id=None, cli_dates=None, force_refresh=False
        )
    monkeypatch.setattr(settings, "TELEGRAM_ACCOUNT_RUNTIME_ENABLED", True)
    monkeypatch.setattr(telegram_collection, "current_claim", lambda: data.claim)
    state = install_provider(monkeypatch, data, FloodWaitError, "flood")
    with actor(data.fixture), pytest.raises(SourceRateLimited):
        await telegram_collection.collect_personal_telegram(None, view, run_id=data.claim.job_id)
    assert state == {"yielded": 1, "sent": 1, "disconnected": True}
