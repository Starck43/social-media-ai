"""DB-exempt latency and lossless unmerge checks."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.models.communications.message import ContactChannelStat
from app.services.communications.ingest import _update_contact_channel_stats
from app.services.contacts import reconcile

__source_only__ = True


@pytest.fixture(autouse=True)
def no_db():
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden")

    event.listen(Dialect, "do_connect", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


class StatsSession:
    def __init__(self):
        self.rows = []

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.rows[0] if self.rows else None)

    def add(self, obj):
        self.rows.append(obj)

    async def flush(self):
        pass


@pytest.mark.asyncio
async def test_latency_observed_once_per_outbound_and_late_events_are_not_samples():
    session = StatsSession()
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)

    async def observe(direction, seconds):
        return await _update_contact_channel_stats(
            session,
            tenant_id=7,
            contact_id=11,
            channel="email",
            direction=direction,
            sent_at=now + timedelta(seconds=seconds),
        )

    stat = await observe("outbound", 0)
    await observe("inbound", 10)
    await observe("inbound", 20)  # no second sample
    await observe("outbound", 30)
    await observe("inbound", 29)  # late, not a measurement
    await observe("inbound", 40)
    assert (stat.inbound_count, stat.outbound_count) == (4, 2)
    assert (stat.latency_total_ms, stat.latency_sample_count) == (20000, 2)
    assert stat.latency_pending_outbound_at is None
    assert stat.last_message_at == now + timedelta(seconds=40)


@pytest.mark.asyncio
async def test_new_outbound_supersedes_pending_and_equal_timestamp_is_ignored():
    session = StatsSession()
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)

    async def observe(direction, seconds):
        return await _update_contact_channel_stats(
            session,
            tenant_id=7,
            contact_id=11,
            channel="email",
            direction=direction,
            sent_at=now + timedelta(seconds=seconds),
        )

    stat = await observe("outbound", 0)
    await observe("outbound", 5)
    await observe("inbound", 5)
    assert stat.latency_sample_count == 0
    await observe("inbound", 8)
    assert (stat.latency_total_ms, stat.latency_sample_count) == (3000, 1)


def test_expected_merged_stats_support_legacy_and_reject_missing_snapshots():
    row = {
        "channel": "email",
        "inbound_count": 2,
        "outbound_count": 1,
        "last_inbound_at": None,
        "last_outbound_at": None,
        "last_message_at": None,
    }
    expected = reconcile._expected_merged_stats({"source_stats_before": [row], "target_stats_before": [row]})
    assert expected[0]["inbound_count"] == 4
    assert expected[0]["latency_total_ms"] == expected[0]["latency_sample_count"] == 0
    assert expected[0]["latency_pending_outbound_at"] is None
    with pytest.raises(reconcile.ContactMergeConflictError):
        reconcile._expected_merged_stats({"source_stats_before": []})


@pytest.mark.asyncio
async def test_unmerge_fails_before_mutation_if_observations_changed(monkeypatch):
    source = SimpleNamespace(id=1, merged_into_id=2)
    target = SimpleNamespace(id=2, merged_into_id=None)
    journal = SimpleNamespace(source_contact_id=1, target_contact_id=2, reason_encrypted="journal")
    stats = ContactChannelStat(
        tenant_id=7,
        contact_id=2,
        channel="email",
        inbound_count=1,
        outbound_count=0,
        latency_total_ms=0,
        latency_sample_count=0,
    )
    snapshot = {"source_before": {}, "target_before": {}, "source_stats_before": [], "target_stats_before": []}
    monkeypatch.setattr(reconcile, "_require_reconcile_permission", lambda action: 7)
    monkeypatch.setattr(reconcile, "decode_merge_journal_payload", lambda raw: {"snapshot": snapshot})

    class Session:
        async def execute(self, stmt):
            sql, params = str(stmt), stmt.compile().params
            if "contact_merge_journal" in sql:
                rows = [journal]
            elif "contact_channel_stats" in sql:
                rows = [stats] if params.get("contact_id_1") == 2 else []
            elif "contacts" in sql:
                rows = [source if params.get("id_1") == 1 else target]
            else:
                raise AssertionError("No mutation is permitted before the guard")
            return SimpleNamespace(
                scalar_one_or_none=lambda: rows[0] if rows else None, scalars=lambda: SimpleNamespace(all=lambda: rows)
            )

    with pytest.raises(reconcile.ContactMergeConflictError, match="observations"):
        await reconcile.unmerge_contacts(Session(), journal_id=10)
    assert source.merged_into_id == 2 and journal.reason_encrypted == "journal"
