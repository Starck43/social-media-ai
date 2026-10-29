"""Import-only P10-T31 contracts; no SQL, providers, schema or migration apply."""

import ast
import importlib
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]
HELPERS = [
    ("agent_scenario", "analysis.agent_scenario", "LLMStrategyType"),
    ("agent_scenario", "analysis.agent_scenario", "TenantScopedMixin"),
    ("ai_analytics", "analysis.ai_analytics", "Decimal"),
    ("ai_analytics", "analysis.ai_analytics", "PeriodType"),
    ("job", "scheduling.job", "TenantScopedMixin"),
]
MODELS = [
    ("agent_scenario", "AgentScenario", "AgentScenarioManager"),
    ("ai_analytics", "AIAnalytics", "AIAnalyticsManager"),
    ("job", "Job", "JobManager"),
]


@pytest.mark.parametrize("legacy_path,canonical_path,name", HELPERS)
def test_helper_is_the_original_canonical_object_and_explicit_export(legacy_path, canonical_path, name):
    legacy = importlib.import_module("app.models." + legacy_path)
    canonical = importlib.import_module("app.models." + canonical_path)
    assert getattr(legacy, name) is getattr(canonical, name)
    assert name in legacy.__all__


@pytest.mark.parametrize("module_name,model_name,manager_name", MODELS)
def test_star_import_keeps_model_manager_and_only_explicit_exports(module_name, model_name, manager_name):
    module = importlib.import_module("app.models." + module_name)
    namespace = {}
    exec("from app.models." + module_name + " import *", namespace)
    assert set(namespace) - {"__builtins__"} == set(module.__all__)
    for name in module.__all__:
        assert namespace[name] is getattr(module, name)
    assert model_name in namespace and manager_name in namespace
    with pytest.raises(AttributeError):
        getattr(module, "UnknownCompatibilitySymbol")


@pytest.mark.parametrize("module_name,model_name,manager_name", MODELS)
def test_reloading_only_the_shim_does_not_rebind_managers_or_register_models(module_name, model_name, manager_name):
    from app.models import Base

    public = importlib.import_module("app.models")
    cls = getattr(public, model_name)
    manager, mapper, table = cls.objects, cls.__mapper__, cls.__table__
    tables = dict(Base.metadata.tables)
    mappers = frozenset(Base.registry.mappers)
    module = importlib.reload(importlib.import_module("app.models." + module_name))
    assert getattr(module, model_name) is cls
    assert cls.objects is manager and manager.model is cls
    assert isinstance(manager, getattr(module, manager_name))
    assert cls.__mapper__ is mapper and cls.__table__ is table
    assert dict(Base.metadata.tables) == tables and frozenset(Base.registry.mappers) == mappers
    assert sum(m.class_ is cls for m in Base.registry.mappers) == 1


@pytest.mark.parametrize(
    "first",
    [
        "app.models.agent_scenario",
        "app.models.ai_analytics",
        "app.models.job",
        "app.models.analysis.agent_scenario",
        "app.models.analysis.ai_analytics",
        "app.models.scheduling.job",
        "app.models.managers.job_manager",
    ],
)
def test_fresh_interpreter_helper_imports_do_not_change_bootstrap_identity(first):
    code = """
import importlib, sys
importlib.import_module(sys.argv[1])
from decimal import Decimal as D
from app.types import LLMStrategyType as L, PeriodType as P
from app.models.base import TenantScopedMixin as T
from app.models.agent_scenario import AgentScenario, AgentScenarioManager, LLMStrategyType, TenantScopedMixin
from app.models.ai_analytics import AIAnalytics, AIAnalyticsManager, Decimal, PeriodType
from app.models.job import Job, JobManager, TenantScopedMixin as JT
from app.models import Base
assert (Decimal, LLMStrategyType, PeriodType, TenantScopedMixin, JT) == (D, L, P, T, T)
for cls, manager in ((AgentScenario, AgentScenarioManager), (AIAnalytics, AIAnalyticsManager), (Job, JobManager)):
    assert isinstance(cls.objects, manager) and cls.objects.model is cls
    assert sum(m.class_ is cls for m in Base.registry.mappers) == 1
    assert cls.__table__ is Base.metadata.tables[cls.__table__.fullname]
"""
    result = subprocess.run([sys.executable, "-c", code, first], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_action_aliases_preserve_canonical_mapper_manager_and_legacy_pickle_paths():
    from app.models import AgentAction, Base, BotAction
    from app.models.managers.bot_action_manager import BotActionManager

    assert AgentAction is BotAction
    assert BotAction.__module__ == "app.models.actions.agent_action"
    assert isinstance(BotAction.objects, BotActionManager) and BotAction.objects.model is BotAction
    assert sum(m.class_ is BotAction for m in Base.registry.mappers) == 1
    for path in ("app.models.bot_action", "app.models.scheduling.bot_action", "app.models.actions.agent_action"):
        assert importlib.import_module(path).BotAction is BotAction
        assert pickle.loads(f"c{path}\nBotAction\n.".encode("ascii")) is BotAction
    assert pickle.loads(pickle.dumps(AgentAction)) is BotAction


def test_0117_header_matches_unchanged_real_parent():
    tree = ast.parse((ROOT / "scripts/migrations/legacy_definitions/0117_capability_entitlements.py").read_text())
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision"}
    }
    assert assignments == {"revision": "0117", "down_revision": "0116"}
    assert "Revision ID: 0117" in ast.get_docstring(tree)
    assert "Revises: 0116" in ast.get_docstring(tree)
