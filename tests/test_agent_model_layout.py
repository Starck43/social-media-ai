"""Prepared agent-model/API compatibility checks; Owner execution only.

Bodies use no SQL or live providers. Shared conftest uses the existing reviewed
schema, so pytest remains sequential across all worktrees sharing test_schema.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import json
import pickle
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "AgentSession": "agent_session",
    "AgentMessage": "agent_message",
    "AgentMemory": "agent_memory",
    "AgentFeedback": "agent_feedback",
}
# Dev0be47bbf class snapshots. Resolve imports, restore canonical append's old
# name and exclude its explicitly tested compatibility wrapper for comparison.
CLASS_FINGERPRINTS = {
    "AgentFeedback": "4d8f1724a7a1015b618a814a66e89300169c728e96dc20adb2cb5dd5112cd590",
    "AgentMemory": "f7ecd5ce520890ed3f472bc5caaeb1b9c24cfb1495c31ed186556e776b44fed7",
    "AgentMessage": "79c18f9c188737a68e5727b6488529dd50e7fd2bba3b230794e7fac4de419493",
    "AgentSession": "47d7171155f4220f7bff0829f66289d3ae64d4142595f9a32de5c68d04097cd3",
}


def _canonical_ast(node):
    if isinstance(node, ast.AST):
        return [type(node).__name__, [[field, _canonical_ast(value)] for field, value in ast.iter_fields(node)]]
    if isinstance(node, list):
        return [_canonical_ast(value) for value in node]
    return node


@pytest.mark.parametrize("name", MODELS)
def test_all_paths_single_mapper_tenant_scope_and_original_manager(name):
    module = MODELS[name]
    public = importlib.import_module("app.models")
    legacy = importlib.import_module(f"app.models.{module}")
    domain = importlib.import_module("app.models.agent")
    leaf = importlib.import_module(f"app.models.agent.{module}")
    manager_module = importlib.import_module(f"app.models.managers.{module}_manager")
    cls = getattr(public, name)
    assert cls is getattr(legacy, name) is getattr(domain, name) is getattr(leaf, name)
    assert cls.__module__ == f"app.models.agent.{module}"
    assert name in domain.__all__ and name in public.__all__
    assert cls._app_label == "social" and cls.__tenant_scoped__
    assert cls.__table__ is public.Base.metadata.tables[cls.__table__.fullname]
    assert "tenant_id" in cls.__table__.c
    assert sum(mapper.class_.__name__ == name for mapper in public.Base.registry.mappers) == 1
    assert cls.objects.model is cls and isinstance(cls.objects, getattr(manager_module, f"{name}Manager"))
    assert getattr(legacy, f"{name}Manager") is getattr(manager_module, f"{name}Manager")


@pytest.mark.parametrize("name", MODELS)
def test_original_class_contract_preserved_except_documented_api_alias(name):
    tree = ast.parse((ROOT / f"app/models/agent/{MODELS[name]}.py").read_text(encoding="utf-8"))
    cls = next(value for value in tree.body if isinstance(value, ast.ClassDef) and value.name == name)
    if name == "AgentSession":
        # The wrapper has separate forwarding/error tests; persistence body is
        # still compared to the entire original append implementation here.
        cls.body = [
            value for value in cls.body if not (isinstance(value, ast.AsyncFunctionDef) and value.name == "append")
        ]
        canonical = next(
            value for value in cls.body if isinstance(value, ast.AsyncFunctionDef) and value.name == "append_message"
        )
        canonical.name = "append"
    for value in ast.walk(cls):
        if isinstance(value, ast.ImportFrom) and value.level:
            value.module = importlib.util.resolve_name("." * value.level + (value.module or ""), "app.models.agent")
            value.level = 0
    payload = json.dumps(_canonical_ast(cls), ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    assert hashlib.sha256(payload).hexdigest() == CLASS_FINGERPRINTS[name]


@pytest.mark.parametrize("name", MODELS)
def test_legacy_and_new_class_serialization_stays_resolvable(name):
    cls = getattr(importlib.import_module("app.models.agent"), name)
    assert pickle.loads(f"capp.models.{MODELS[name]}\n{name}\n.".encode("ascii")) is cls
    assert pickle.loads(pickle.dumps(cls)) is cls


def test_unique_foreign_key_and_cost_role_contracts_do_not_change():
    from sqlalchemy import Float, String, UniqueConstraint
    from app.models.agent import AgentFeedback, AgentMemory, AgentMessage, AgentSession

    def unique_columns(model, name):
        value = next(c for c in model.__table__.constraints if isinstance(c, UniqueConstraint) and c.name == name)
        return tuple(c.name for c in value.columns)

    assert unique_columns(AgentSession, "uq_agent_session_chat") == ("channel", "chat_id")
    assert unique_columns(AgentMemory, "uq_agent_memory_tenant_scope_key") == ("tenant_id", "scope", "key")
    assert isinstance(AgentMessage.__table__.c.role.type, String)
    assert isinstance(AgentMessage.__table__.c.cost.type, Float) and AgentMessage.__table__.c.cost.nullable
    for model, column, table, ondelete in [
        (AgentMessage, "session_id", "agent_sessions", "CASCADE"),
        (AgentFeedback, "session_id", "agent_sessions", "CASCADE"),
        (AgentFeedback, "message_id", "agent_messages", "SET NULL"),
        (AgentMemory, "evidence_message_id", "agent_messages", "SET NULL"),
    ]:
        fk = next(iter(model.__table__.c[column].foreign_keys))
        assert fk.column.table.name == table and fk.ondelete == ondelete


@pytest.mark.parametrize(
    "state,expected",
    [
        (None, None),
        ({}, None),
        ({"pending_confirmation": "bad-shape"}, None),
        ({"pending_confirmation": {"tool": "example"}}, {"tool": "example"}),
    ],
)
def test_session_state_readers_preserve_validation(state, expected):
    from app.models.agent import AgentSession

    session = SimpleNamespace(state=state)
    assert AgentSession.pending_confirmation.fget(session) == expected
    assert AgentSession.update_offset.fget(session) == (state or {}).get("update_offset")


@pytest.mark.parametrize("entry", ["append", "append_message"])
async def test_both_append_names_persist_then_touch_once(monkeypatch, entry):
    from app.models.agent import AgentSession
    from app.models.managers import agent_message_manager, agent_session_manager

    events = []
    message = object()

    async def persist(**kwargs):
        events.append(("persist", kwargs))
        return message

    async def touch(session_id):
        events.append(("touch", session_id))

    messages = SimpleNamespace(append=AsyncMock(side_effect=persist))
    sessions = SimpleNamespace(touch=AsyncMock(side_effect=touch))
    monkeypatch.setattr(agent_message_manager, "agent_messages", messages)
    monkeypatch.setattr(agent_session_manager, "agent_sessions", sessions)
    session = AgentSession(id=17)
    tool_calls = [{"id": "synthetic-tool-call"}]
    assert await getattr(session, entry)("assistant", None, tool_calls=tool_calls, tool_call_id="call-1") is message
    expected = dict(session_id=17, role="assistant", content=None, tool_calls=tool_calls, tool_name="call-1")
    messages.append.assert_awaited_once_with(**expected)
    sessions.touch.assert_awaited_once_with(17)
    assert events == [("persist", expected), ("touch", 17)]


@pytest.mark.parametrize("entry", ["append", "append_message"])
async def test_append_storage_failure_propagates_without_touch_or_retry(monkeypatch, entry):
    from app.models.agent import AgentSession
    from app.models.managers import agent_message_manager, agent_session_manager

    messages = SimpleNamespace(append=AsyncMock(side_effect=RuntimeError("synthetic-storage-failure")))
    sessions = SimpleNamespace(touch=AsyncMock())
    monkeypatch.setattr(agent_message_manager, "agent_messages", messages)
    monkeypatch.setattr(agent_session_manager, "agent_sessions", sessions)
    with pytest.raises(RuntimeError, match="synthetic-storage-failure"):
        await getattr(AgentSession(id=17), entry)("user", "synthetic-text")
    messages.append.assert_awaited_once()
    sessions.touch.assert_not_awaited()


async def test_compatibility_append_delegates_to_canonical_api_once():
    from app.models.agent import AgentSession

    expected = object()
    session = SimpleNamespace(append_message=AsyncMock(return_value=expected))
    assert await AgentSession.append(session, "tool", "result", tool_call_id="call-1") is expected
    session.append_message.assert_awaited_once_with("tool", "result", tool_calls=None, tool_call_id="call-1")


async def test_save_state_remains_replace_not_merge_and_uses_original_singleton(monkeypatch):
    from app.models.agent import AgentSession
    from app.models.managers import agent_session_manager

    manager = SimpleNamespace(update_by_id=AsyncMock())
    monkeypatch.setattr(agent_session_manager, "agent_sessions", manager)
    session = AgentSession(id=17, state={"old": "value"})
    state = {"update_offset": 0}
    await session.save_state(state)
    manager.update_by_id.assert_awaited_once_with(17, state=state)
    assert session.state is state and "old" not in session.state


@pytest.mark.parametrize("entry", ["append", "append_message"])
async def test_touch_failure_propagates_without_replaying_persistence(monkeypatch, entry):
    from app.models.agent import AgentSession
    from app.models.managers import agent_message_manager, agent_session_manager

    messages = SimpleNamespace(append=AsyncMock(return_value=object()))
    sessions = SimpleNamespace(touch=AsyncMock(side_effect=RuntimeError("synthetic-touch-failure")))
    monkeypatch.setattr(agent_message_manager, "agent_messages", messages)
    monkeypatch.setattr(agent_session_manager, "agent_sessions", sessions)
    with pytest.raises(RuntimeError, match="synthetic-touch-failure"):
        await getattr(AgentSession(id=17), entry)("assistant", "synthetic-text")
    messages.append.assert_awaited_once()
    sessions.touch.assert_awaited_once_with(17)


def test_unknown_export_is_rejected():
    with pytest.raises(AttributeError):
        getattr(importlib.import_module("app.models.agent"), "UnknownAgentModel")


@pytest.mark.parametrize(
    "first",
    [
        "app.models.agent",
        "app.models.agent_session",
        "app.models.agent_message",
        "app.models.agent_memory",
        "app.models.agent_feedback",
        "app.models.agent.agent_session",
        "app.models.agent.agent_message",
        "app.models.agent.agent_memory",
        "app.models.agent.agent_feedback",
        "app.models.managers.agent_session_manager",
        "app.models.managers.agent_message_manager",
        "app.models.managers.agent_memory_manager",
        "app.models.managers.agent_feedback_manager",
    ],
)
def test_fresh_process_initialization_preserves_classes_and_manager_singletons(first):
    code = """
import importlib
import json
import sys
importlib.import_module(sys.argv[1])
public = importlib.import_module('app.models')
domain = importlib.import_module('app.models.agent')
for name, module in json.loads(sys.argv[2]).items():
    cls = getattr(public, name)
    assert cls is getattr(domain, name) is getattr(importlib.import_module(f'app.models.{module}'), name)
    assert cls.objects.model is cls
    assert sum(mapper.class_.__name__ == name for mapper in public.Base.registry.mappers) == 1
session = domain.AgentSession
assert callable(session.append) and callable(session.append_message)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, first, json.dumps(MODELS)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
