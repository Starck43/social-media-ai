"""Prepared scheduling-layout checks; Owner executes in the reviewed environment.

No SQL/provider/sender calls in bodies; shared conftest uses existing test_schema.
This does not implement or certify queue leases, claims, transactions or outbox.
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

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODELS = {"AgentTask": "agent_task", "Job": "job", "DigestRun": "digest_run", "BotAction": "bot_action"}
# Whole-class/association snapshots from dev1f66f348, imports resolved to their original targets.
CONTRACT_FINGERPRINTS = {
    "AgentTask": "a463ca229b6108ca87c599c50ca87af75fb882f60a0f443f638ba7f4db27122f",
    "BotAction": "c96dffa6d05a5a1479ff671220396a42e5f7207d0684ee1b5cb183473e26e72e",
    "DigestRun": "7114fc739374a5bba0c838989b281ee7e553bf39f4b82e8733af44374120e284",
    "Job": "a297174ce74fc84d034ced35ce0537343753e4b7b9d4aca4b4c7e7f9de0395e2",
    "agent_task_sources": "cae70099aaa00c5f9ccd7a7a40195a8f00909deaa1cf488094f0c4b9116d0d5b",
}


def _canonical_ast(node):
    if isinstance(node, ast.AST):
        return [type(node).__name__, [[field, _canonical_ast(value)] for field, value in ast.iter_fields(node)]]
    if isinstance(node, list):
        return [_canonical_ast(value) for value in node]
    return node


def _fingerprint(node):
    for value in ast.walk(node):
        if isinstance(value, ast.ImportFrom) and value.level:
            value.module = importlib.util.resolve_name(
                "." * value.level + (value.module or ""), "app.models.scheduling"
            )
            value.level = 0
    payload = json.dumps(_canonical_ast(node), ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("name", MODELS)
def test_paths_single_mapper_manager_and_tenant_scope_preserved(name):
    module = MODELS[name]
    public = importlib.import_module("app.models")
    legacy = importlib.import_module(f"app.models.{module}")
    domain = importlib.import_module("app.models.scheduling")
    leaf = importlib.import_module(f"app.models.scheduling.{module}")
    manager_module = importlib.import_module(f"app.models.managers.{module}_manager")
    cls = getattr(public, name)
    assert cls is getattr(legacy, name) is getattr(domain, name) is getattr(leaf, name)
    assert name in public.__all__ and name in domain.__all__
    assert cls.__module__ == f"app.models.scheduling.{module}"
    assert cls.__table__ is public.Base.metadata.tables[cls.__table__.fullname]
    assert cls._app_label == "social" and cls.__tenant_scoped__
    assert "tenant_id" in cls.__table__.c
    assert sum(mapper.class_.__name__ == name for mapper in public.Base.registry.mappers) == 1
    assert isinstance(cls.objects, getattr(manager_module, f"{name}Manager")) and cls.objects.model is cls
    assert getattr(legacy, f"{name}Manager") is getattr(manager_module, f"{name}Manager")


@pytest.mark.parametrize("name", MODELS)
def test_entire_original_class_contract_unchanged(name):
    tree = ast.parse((ROOT / f"app/models/scheduling/{MODELS[name]}.py").read_text(encoding="utf-8"))
    cls = next(value for value in tree.body if isinstance(value, ast.ClassDef) and value.name == name)
    assert _fingerprint(cls) == CONTRACT_FINGERPRINTS[name]


@pytest.mark.parametrize("name", MODELS)
def test_legacy_and_current_class_serialization_resolve(name):
    cls = getattr(importlib.import_module("app.models.scheduling"), name)
    assert pickle.loads(f"capp.models.{MODELS[name]}\n{name}\n.".encode("ascii")) is cls
    assert pickle.loads(pickle.dumps(cls)) is cls


def test_task_source_association_has_single_identity_and_unchanged_contract():
    from sqlalchemy.orm import configure_mappers
    from app.models import AgentScenario, AgentTask, Base, Source, Tenant
    from app.models.agent_task import agent_task_sources as legacy
    from app.models.scheduling import agent_task_sources as domain
    from app.models.scheduling.agent_task import agent_task_sources as leaf

    configure_mappers()
    assert legacy is domain is leaf is Base.metadata.tables[leaf.fullname]
    assert sum(table.name == "agent_task_sources" for table in Base.metadata.tables.values()) == 1
    assert tuple(column.name for column in leaf.primary_key.columns) == ("agent_task_id", "source_id")
    assert AgentTask.__mapper__.relationships["sources"].secondary is leaf
    assert Source.__mapper__.relationships["agent_tasks"].secondary is leaf
    assert AgentTask.__mapper__.relationships["agent_scenario"].mapper.class_ is AgentScenario
    assert AgentTask.__mapper__.relationships["tenant"].mapper.class_ is Tenant
    tree = ast.parse((ROOT / "app/models/scheduling/agent_task.py").read_text(encoding="utf-8"))
    association = next(
        value
        for value in tree.body
        if isinstance(value, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "agent_task_sources" for target in value.targets)
    )
    assert _fingerprint(association) == CONTRACT_FINGERPRINTS["agent_task_sources"]


def test_action_relationships_and_guard_defaults_preserved():
    from app.models import AIAnalytics, AgentScenario, AgentTask, BotAction, Source

    for field, target in [
        ("scenario", AgentScenario),
        ("task", AgentTask),
        ("source", Source),
        ("analytics", AIAnalytics),
    ]:
        assert BotAction.__mapper__.relationships[field].mapper.class_ is target
    assert BotAction.__table__.c.dry_run.default.arg is True
    assert str(BotAction.__table__.c.dry_run.server_default.arg) == "true"
    assert BotAction.__table__.c.confirmed_by.nullable and BotAction.__table__.c.confirmed_at.nullable


def test_existing_status_and_cost_types_and_checkpoint_unknown_state_preserved():
    from sqlalchemy import Float, String
    from sqlalchemy.dialects import postgresql
    from app.models import BotAction, DigestRun, Job
    from app.types import BotActionStatus

    for model in (Job, DigestRun):
        assert isinstance(model.__table__.c.status.type, String)
        assert isinstance(model.__table__.c.llm_cost.type, Float) and model.__table__.c.llm_cost.nullable
    assert BotAction.__table__.c.status.type.enum_class is BotActionStatus
    checkpoint = DigestRun.__table__.c.delivery_state
    assert checkpoint.nullable
    assert checkpoint.default is None and checkpoint.server_default is None
    assert checkpoint.type.compile(dialect=postgresql.dialect()) == "JSONB"


def test_named_uniqueness_and_nullable_task_foreign_keys_preserved():
    from sqlalchemy import UniqueConstraint
    from app.models import AgentTask, DigestRun, Job

    for model, name, expected in [
        (AgentTask, "uq_agent_task_tenant_name", ("tenant_id", "name")),
        (DigestRun, "uq_digest_agent_task_period", ("agent_task_id", "period_start", "period_end")),
    ]:
        constraint = next(
            value for value in model.__table__.constraints if isinstance(value, UniqueConstraint) and value.name == name
        )
        assert tuple(column.name for column in constraint.columns) == expected
    for model in (Job, DigestRun):
        column = model.__table__.c.agent_task_id
        assert column.nullable
        fk = next(iter(column.foreign_keys))
        assert fk.column.table.name == "agent_tasks" and fk.ondelete == "SET NULL"


@pytest.mark.parametrize(
    "module,symbol",
    [
        ("agent_task", "AgentActionType"),
        ("agent_task", "BotTriggerType"),
        ("bot_action", "BotActionStatus"),
        ("bot_action", "AgentActionType"),
        ("digest_run", "JSONB"),
        ("job", "TenantScopedMixin"),
    ],
)
def test_existing_legacy_symbols_remain_available(module, symbol):
    legacy = importlib.import_module(f"app.models.{module}")
    leaf = importlib.import_module(f"app.models.scheduling.{module}")
    assert getattr(legacy, symbol) is getattr(leaf, symbol)


def test_unknown_export_raises_attribute_error():
    with pytest.raises(AttributeError):
        getattr(importlib.import_module("app.models.scheduling"), "UnknownSchedulingModel")


@pytest.mark.parametrize(
    "first",
    [
        "app.models.scheduling",
        "app.models.agent_task",
        "app.models.job",
        "app.models.digest_run",
        "app.models.bot_action",
        "app.models.scheduling.agent_task",
        "app.models.scheduling.job",
        "app.models.scheduling.digest_run",
        "app.models.scheduling.bot_action",
        "app.models.managers.agent_task_manager",
        "app.models.managers.job_manager",
        "app.models.managers.digest_run_manager",
        "app.models.managers.bot_action_manager",
    ],
)
def test_fresh_process_model_manager_and_association_identity(first):
    code = """
import importlib
import json
import sys
importlib.import_module(sys.argv[1])
public = importlib.import_module('app.models')
domain = importlib.import_module('app.models.scheduling')
for name, module in json.loads(sys.argv[2]).items():
    cls = getattr(public, name)
    assert cls is getattr(domain, name) is getattr(importlib.import_module(f'app.models.{module}'), name)
    assert cls.objects.model is cls
    assert sum(mapper.class_.__name__ == name for mapper in public.Base.registry.mappers) == 1
assert domain.agent_task_sources is importlib.import_module('app.models.agent_task').agent_task_sources
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
