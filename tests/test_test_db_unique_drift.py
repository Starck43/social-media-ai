"""Unique-constraint drift diff — the schema bootstrap repair must stay honest.

Covers `scripts.setup_test_db.unique_constraint_drift`, the pure decision behind
aligning the model-driven test schema with the models: `create_all` can add a
table but never rewrite an existing table's UNIQUE constraint, so a model that
widens one (agent_sessions gaining tenant_id) would otherwise leave the stale
constraint in place and every later run would test a schema nobody chose.

Run: python tests/test_test_db_unique_drift.py
No database, no app import; only the bootstrap module and stdlib.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.setup_test_db import unique_constraint_drift  # noqa: E402


def test_rename_and_widen_is_repaired():
    """The real regression: session uniqueness widened from (channel, chat_id)."""
    model = {"agent_sessions": {"uq_agent_session_tenant_channel_chat": ("channel", "chat_id", "tenant_id")}}
    db = {"agent_sessions": {"uq_agent_session_chat": ("channel", "chat_id")}}

    stale, missing = unique_constraint_drift(model, db)

    assert stale == [("agent_sessions", "uq_agent_session_chat")]
    assert missing == [("agent_sessions", ("channel", "chat_id", "tenant_id"), "uq_agent_session_tenant_channel_chat")]


def test_matching_schema_is_left_alone():
    model = {
        "agent_sessions": {"uq_agent_session_tenant_channel_chat": ("channel", "chat_id", "tenant_id")},
        "tenants": {"uq_tenant_slug": ("slug",)},
    }
    db = {
        "agent_sessions": {"uq_agent_session_tenant_channel_chat": ("channel", "chat_id", "tenant_id")},
        "tenants": {"uq_tenant_slug": ("slug",)},
    }

    assert unique_constraint_drift(model, db) == ([], [])


def test_column_unique_auto_name_is_not_stale():
    """Column(unique=True) has no declared name; the db calls it *_key."""
    model = {"llm_providers": {"cols:name": ("name",)}}
    db = {"llm_providers": {"llm_providers_name_key": ("name",)}}

    assert unique_constraint_drift(model, db) == ([], [])


def test_model_constraint_absent_in_db_is_created():
    model = {"agent_memory": {"uq_agent_memory_tenant_scope_key": ("key", "scope", "tenant_id")}}
    db = {"agent_memory": {}}

    stale, missing = unique_constraint_drift(model, db)

    assert stale == []
    assert missing == [("agent_memory", ("key", "scope", "tenant_id"), "uq_agent_memory_tenant_scope_key")]


def test_constraint_no_longer_in_models_is_dropped():
    model = {"sources": {"uq_source_tenant_platform_external": ("external_id", "platform_id", "tenant_id")}}
    db = {
        "sources": {
            "uq_source_tenant_platform_external": ("external_id", "platform_id", "tenant_id"),
            "uq_source_old_key": ("legacy_id",),
        }
    }

    stale, missing = unique_constraint_drift(model, db)

    assert stale == [("sources", "uq_source_old_key")]
    assert missing == []


def test_only_the_drifted_table_is_reported():
    model = {
        "agent_sessions": {"uq_agent_session_tenant_channel_chat": ("channel", "chat_id", "tenant_id")},
        "tenants": {"uq_tenant_slug": ("slug",)},
    }
    db = {
        "agent_sessions": {"uq_agent_session_chat": ("channel", "chat_id")},
        "tenants": {"uq_tenant_slug": ("slug",)},
    }

    stale, missing = unique_constraint_drift(model, db)

    assert stale == [("agent_sessions", "uq_agent_session_chat")]
    assert missing == [("agent_sessions", ("channel", "chat_id", "tenant_id"), "uq_agent_session_tenant_channel_chat")]
