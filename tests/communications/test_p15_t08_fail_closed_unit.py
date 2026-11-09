"""Reply marker never grants outbox authority during staged implementation."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.communications import outbox_store as module
from app.services.communications.outbox_contracts import ScheduleIn


@pytest.mark.asyncio
async def test_required_evidence_cannot_schedule_without_reply_service(monkeypatch):
    monkeypatch.setattr(module, "current_actor", AsyncMock(return_value=(1, 1)))
    monkeypatch.setattr(module, "owned_message", AsyncMock(return_value=SimpleNamespace(reply_evidence_required=True)))
    monkeypatch.setattr(module, "targets_for", AsyncMock())
    request = ScheduleIn(
        message_id=1,
        expected_content_hash="sha256:" + "a" * 64,
        scheduled_at=datetime.now(timezone.utc),
    )
    with pytest.raises(module.OutboxConflict, match="^outbox_reply_not_implemented$"):
        await module.outbox_store.schedule(request, session=object())
    module.targets_for.assert_not_awaited()


@pytest.mark.parametrize("claimed", [False, True])
def test_required_evidence_cannot_claim_or_deliver_without_reply_service(claimed):
    message = SimpleNamespace(reply_evidence_required=True)
    with pytest.raises(module.OutboxConflict, match="^outbox_reply_not_implemented$"):
        module.verify_approval(message, [], None, None, [], datetime.now(timezone.utc), claimed=claimed)
