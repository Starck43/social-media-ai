"""Migration 0144 contract tests; no SQL is executed."""

import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

__source_only__ = True
MIGRATION = Path(__file__).resolve().parents[2] / "migrations/versions/0144_contact_channel_latency.py"


@pytest.fixture(autouse=True)
def no_db():
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden")

    event.listen(Dialect, "do_connect", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


def migration():
    return runpy.run_path(str(MIGRATION))


def test_revision_and_additive_unmeasured_defaults(monkeypatch):
    m = migration()
    assert m["revision"] == "0144"
    assert m["down_revision"] == "0143"
    operations = []
    fake = SimpleNamespace(
        add_column=lambda table, column, **kw: operations.append((table, column, kw)),
        create_check_constraint=lambda *a, **kw: None,
    )
    monkeypatch.setitem(m["upgrade"].__globals__, "op", fake)
    m["upgrade"]()
    columns = {column.name: column for _, column, _ in operations}
    assert set(columns) == {"latency_total_ms", "latency_sample_count", "latency_pending_outbound_at"}
    assert not columns["latency_sample_count"].nullable
    assert str(columns["latency_sample_count"].server_default.arg) == "0"
    assert columns["latency_pending_outbound_at"].nullable


@pytest.mark.parametrize(
    "stat",
    [
        {"latency_sample_count": 1},
        {"latency_total_ms": 1000},
        {"latency_pending_outbound_at": "2026-11-07T12:00:00+00:00"},
    ],
)
def test_journal_observations_prevent_downgrade(stat):
    import json

    inspect = migration()["_journal_has_observations"]
    assert inspect(json.dumps({"snapshot": {"source_stats_before": [stat]}}))
    assert not inspect(json.dumps({"snapshot": {"source_stats_before": [{"inbound_count": 10}]}}))
    with pytest.raises(RuntimeError):
        inspect("not-decodable")


@pytest.mark.parametrize(
    "stats_have_samples,journal",
    [(True, None), (False, '{"snapshot":{"target_stats_before":[{"latency_sample_count":1}]}}')],
)
def test_downgrade_refuses_data_loss_before_dropping(monkeypatch, stats_have_samples, journal):
    m = migration()
    calls = []

    class Bind:
        dialect = SimpleNamespace(identifier_preparer=SimpleNamespace(quote_schema=lambda value: '"' + value + '"'))

        def execute(self, stmt):
            text = str(stmt)
            calls.append(text)
            if text.startswith("LOCK"):
                return None
            if "SELECT EXISTS" in text:
                return SimpleNamespace(scalar=lambda: stats_have_samples)
            return iter([(journal,)])

    drops = []
    fake = SimpleNamespace(
        get_bind=lambda: Bind(),
        drop_column=lambda *a, **kw: drops.append(a),
        drop_constraint=lambda *a, **kw: drops.append(a),
    )
    monkeypatch.setitem(m["downgrade"].__globals__, "op", fake)
    with pytest.raises(RuntimeError, match="discard"):
        m["downgrade"]()
    assert calls[0].startswith("LOCK TABLE") and not drops


def test_downgrade_allows_unmeasured_legacy_rows(monkeypatch):
    m = migration()

    class Bind:
        dialect = SimpleNamespace(identifier_preparer=SimpleNamespace(quote_schema=lambda value: '"' + value + '"'))

        def execute(self, stmt):
            text = str(stmt)
            if text.startswith("LOCK"):
                return None
            if "SELECT EXISTS" in text:
                return SimpleNamespace(scalar=lambda: False)
            return iter([('{"snapshot":{"source_stats_before":[{"inbound_count":3}]}}',)])

    drops = []
    fake = SimpleNamespace(
        get_bind=lambda: Bind(),
        drop_column=lambda *a, **kw: drops.append(a),
        drop_constraint=lambda *a, **kw: drops.append(a),
    )
    monkeypatch.setitem(m["downgrade"].__globals__, "op", fake)
    m["downgrade"]()
    assert len(drops) == 5
