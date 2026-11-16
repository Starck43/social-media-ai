"""Canonical model package and action-alias contracts."""

import importlib
from pathlib import Path

from sqlalchemy.orm import configure_mappers

from app.models import AgentAction, Base, BotAction

ROOT = Path(__file__).parents[2] / "app/models"


def test_one_action_mapper_and_canonical_path():
    configure_mappers()
    assert AgentAction is BotAction
    assert sum(m.class_ is AgentAction for m in Base.registry.mappers) == 1
    canonical = importlib.import_module("app.models.actions.agent_action")
    assert canonical.AgentAction is canonical.BotAction is AgentAction
    assert AgentAction.__module__ == "app.models.actions.agent_action"


def test_model_root_contains_only_facade_and_shared_infrastructure():
    expected = {"__init__.py", "base.py", "bootstrap.py", "scenario_schema_guard.py"}
    assert {path.name for path in ROOT.glob("*.py")} == expected
