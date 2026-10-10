"""Analysis-layout regressions prepared for Owner execution, NOT run by agent.

Bodies do not issue SQL or call a provider; shared conftest uses the existing
reviewed test schema. Run sequentially across worktrees sharing that schema.
No migration, schema reset/drop/stamp or live crypto/provider action here.
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

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "AgentScenario": ("agent_scenario", "agent_scenarios", "AgentScenarioManager", "social", True),
    "AIAnalytics": ("ai_analytics", "ai_analytics", "AIAnalyticsManager", "dashboard", True),
    "LLMModel": ("llm_model", "llm_models", "LLMModelManager", "ai", False),
    "LLMProvider": ("llm_provider", "llm_providers", "LLMProviderManager", "ai", False),
}
# Entire-class snapshots from dev 0be47bbf, normalizing relative import targets.
CLASS_FINGERPRINTS = {'AIAnalytics': '783bf3b00f832e2782671b11ea1b3d30e66f77e2b5e72c41c5512b5d66f7a1d7',
 'AgentScenario': 'ef147cce4370d511a3d120ec9da1ff1eed8646ecd9b0b31b5a75e386ccf19352',
 'LLMModel': '0bdd90b75c7687a3e1e74efaabd7fc47b72a48e25600d1e8c742cef5a6e68e28',
 'LLMProvider': '5ede3fd75631590d2e2a88434cddf6ed299d92f58f355ac5b0d41c9a8184efbf'}


def _canonical_ast(node):
    # Explicit empty/None fields avoid Python 3.12/3.13 ast.dump differences.
    if isinstance(node, ast.AST):
        return [type(node).__name__, [[field, _canonical_ast(value)] for field, value in ast.iter_fields(node)]]
    if isinstance(node, list):
        return [_canonical_ast(value) for value in node]
    return node


@pytest.mark.parametrize("name", MODELS)
def test_public_legacy_domain_leaf_identity_and_manager_binding(name):
    module, table, manager, label, scoped = MODELS[name]
    public = importlib.import_module("app.models")
    legacy = importlib.import_module(f"app.models.{module}")
    domain = importlib.import_module("app.models.analysis")
    leaf = importlib.import_module(f"app.models.analysis.{module}")
    manager_module = importlib.import_module(f"app.models.managers.{module}_manager")
    cls = getattr(public, name)
    assert cls is getattr(legacy, name) is getattr(domain, name) is getattr(leaf, name)
    assert name in public.__all__ and name in domain.__all__
    assert domain.__dict__[name] is cls
    assert cls.__module__ == f"app.models.analysis.{module}"
    assert cls._app_label == label and cls.__tablename__ == table
    assert cls.__table__ is public.Base.metadata.tables[cls.__table__.fullname]
    assert sum(mapper.class_.__name__ == name for mapper in public.Base.registry.mappers) == 1
    assert isinstance(cls.objects, getattr(manager_module, manager))
    assert cls.objects.model is cls
    assert getattr(legacy, manager) is getattr(manager_module, manager)
    assert bool(getattr(cls, "__tenant_scoped__", False)) is scoped
    assert ("tenant_id" in cls.__table__.c) is scoped


@pytest.mark.parametrize("name", MODELS)
def test_entire_class_contract_unchanged_except_relative_import_depth(name):
    module = MODELS[name][0]
    tree = ast.parse((ROOT / f"app/models/analysis/{module}.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
    for node in ast.walk(cls):
        if isinstance(node, ast.ImportFrom) and node.level:
            node.module = importlib.util.resolve_name(
                "." * node.level + (node.module or ""), "app.models.analysis"
            )
            node.level = 0
    payload = json.dumps(_canonical_ast(cls), ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    assert hashlib.sha256(payload).hexdigest() == CLASS_FINGERPRINTS[name]


@pytest.mark.parametrize("module,symbol", [
    ("agent_scenario", "LLMStrategyType"), ("ai_analytics", "PeriodType"),
    ("ai_analytics", "Decimal"), ("llm_provider", "Base"),
    ("llm_model", "TimestampMixin"), ("agent_scenario", "TenantScopedMixin"),
    ("llm_model", "_model_type_to_capabilities"),
])
def test_legacy_imported_symbols_and_capability_helper_preserved(module, symbol):
    legacy = importlib.import_module(f"app.models.{module}")
    leaf = importlib.import_module(f"app.models.analysis.{module}")
    assert getattr(legacy, symbol) is getattr(leaf, symbol)


@pytest.mark.parametrize("name", MODELS)
def test_legacy_and_new_serialized_class_references_resolve(name):
    module = MODELS[name][0]
    cls = getattr(importlib.import_module(f"app.models.{module}"), name)
    # Trusted locally constructed class reference, never external pickle input.
    assert pickle.loads(f"capp.models.{module}\n{name}\n.".encode("ascii")) is cls
    assert pickle.loads(pickle.dumps(cls)) is cls


def test_relationships_across_domain_boundaries_are_unchanged():
    from sqlalchemy.orm import configure_mappers
    from app.models import AIAnalytics, AgentScenario, AgentTask, LLMModel, LLMProvider, Source

    configure_mappers()
    assert LLMProvider.__mapper__.relationships["models"].mapper.class_ is LLMModel
    assert LLMModel.__mapper__.relationships["provider"].mapper.class_ is LLMProvider
    for media in ("text", "image", "video"):
        assert AgentScenario.__mapper__.relationships[f"{media}_llm_model"].mapper.class_ is LLMModel
        assert LLMModel.__mapper__.relationships[f"{media}_scenarios"].mapper.class_ is AgentScenario
    assert AgentScenario.__mapper__.relationships["agent_tasks"].mapper.class_ is AgentTask
    assert AIAnalytics.__mapper__.relationships["source"].mapper.class_ is Source
    assert Source.__mapper__.relationships["analytics"].mapper.class_ is AIAnalytics
    assert AIAnalytics.__mapper__.relationships["parent"].mapper.class_ is AIAnalytics
    assert AIAnalytics.__mapper__.relationships["children"].mapper.class_ is AIAnalytics


def test_cost_types_and_period_enum_are_not_unified_by_layout_change():
    from sqlalchemy import Float, Numeric
    from app.models.analysis import AIAnalytics, LLMModel
    from app.types import PeriodType

    cost = AIAnalytics.__table__.c.estimated_cost
    assert isinstance(cost.type, Numeric)
    assert (cost.type.precision, cost.type.scale) == (14, 6)
    assert cost.nullable and "USD cents" in cost.comment
    period = AIAnalytics.__table__.c.period_type
    assert period.type.enum_class is None
    assert period.type.impl.enums == [member.name for member in PeriodType]
    assert period.type.process_bind_param(PeriodType.DAY, None) == PeriodType.DAY.name
    assert period.type.process_result_value(PeriodType.DAY.name, None) is PeriodType.DAY
    for field in ("input_cost_per_1k", "output_cost_per_1k", "last_request_cost"):
        assert isinstance(LLMModel.__table__.c[field].type, Float)
    assert LLMModel.__table__.c.last_request_cost.nullable


@pytest.mark.parametrize("value,expected", [
    ("", []), ("text", ["text"]), ("text, image,video", ["text", "image", "video"]),
])
def test_capabilities_and_legacy_helper_preserved(value, expected):
    from app.models.analysis import LLMModel
    from app.models.llm_model import _model_type_to_capabilities

    assert _model_type_to_capabilities(value) == expected
    assert LLMModel.capabilities.fget(SimpleNamespace(model_type=value)) == expected


def test_model_tariff_conversion_and_scenario_legacy_prompt_alias_preserved():
    from app.models.analysis import AgentScenario, LLMModel

    assert LLMModel.get_cost_per_million(
        SimpleNamespace(input_cost_per_1k=0.001, output_cost_per_1k=0.002)
    ) == (1.0, 2.0)
    scenario = SimpleNamespace(base_prompt="original")
    assert AgentScenario.ai_prompt.fget(scenario) == "original"
    AgentScenario.ai_prompt.fset(scenario, "updated")
    assert scenario.base_prompt == "updated"


@pytest.mark.parametrize("stored", [None, "", "synthetic-ciphertext"])
def test_provider_crypto_import_still_targets_app_utils(monkeypatch, stored):
    from app.models.analysis import LLMProvider
    from app.utils import crypto

    calls = []

    def fake_decrypt(value):
        calls.append(value)
        return "synthetic-plaintext"

    monkeypatch.setattr(crypto, "decrypt_secret", fake_decrypt)
    result = LLMProvider.get_api_key(SimpleNamespace(encrypted_api_key=stored))
    assert result == ("synthetic-plaintext" if stored else "")
    assert calls == ([stored] if stored else [])


@pytest.mark.parametrize("key,expected", [
    ("", ""), ("short", "***"), ("synthetic-long-token", "synt…oken"),
])
def test_provider_key_masking_unchanged_without_real_secrets(key, expected):
    from app.models.analysis import LLMProvider

    assert LLMProvider.decrypted_key_masked(SimpleNamespace(get_api_key=lambda: key)) == expected


def test_unknown_domain_export_raises_attribute_error():
    domain = importlib.import_module("app.models.analysis")
    with pytest.raises(AttributeError):
        getattr(domain, "UnknownAnalysisModel")


@pytest.mark.parametrize("first", [
    "app.models.analysis",
    "app.models.agent_scenario", "app.models.ai_analytics", "app.models.llm_model", "app.models.llm_provider",
    "app.models.analysis.agent_scenario", "app.models.analysis.ai_analytics",
    "app.models.analysis.llm_model", "app.models.analysis.llm_provider",
    "app.models.managers.agent_scenario_manager", "app.models.managers.ai_analytics_manager",
    "app.models.managers.llm_model_manager", "app.models.managers.llm_provider_manager",
])
def test_fresh_process_import_entry_points_complete_without_partial_binding(first):
    # Owner execution inherits conftest's redirected env; child performs no SQL.
    code = """
import importlib
import json
import sys
importlib.import_module(sys.argv[1])
public = importlib.import_module('app.models')
domain = importlib.import_module('app.models.analysis')
for name, module in json.loads(sys.argv[2]).items():
    cls = getattr(public, name)
    legacy = importlib.import_module(f'app.models.{module}')
    leaf = importlib.import_module(f'app.models.analysis.{module}')
    assert cls is getattr(legacy, name) is getattr(domain, name) is getattr(leaf, name)
    assert cls.objects.model is cls
    assert sum(mapper.class_.__name__ == name for mapper in public.Base.registry.mappers) == 1
"""
    result = subprocess.run(
        [sys.executable, "-c", code, first, json.dumps({name: data[0] for name, data in MODELS.items()})],
        cwd=ROOT, text=True, capture_output=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
