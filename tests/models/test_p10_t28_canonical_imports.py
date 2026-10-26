"""Pure compatibility aliases and canonical model imports inside managers."""

import ast
import importlib
from pathlib import Path

from sqlalchemy.orm import configure_mappers

from app.models import AgentAction, Base, BotAction

ROOT = Path(__file__).parents[2] / "app/models"


def test_one_action_mapper_and_legacy_paths():
    configure_mappers()
    assert AgentAction is BotAction
    assert sum(m.class_ is AgentAction for m in Base.registry.mappers) == 1
    for path in ["app.models.bot_action", "app.models.scheduling.bot_action", "app.models.actions.agent_action"]:
        assert importlib.import_module(path).BotAction is AgentAction


def test_root_shims_are_pure_explicit_aliases_and_managers_import_domains():
    shims = set()
    for path in ROOT.glob("*.py"):
        if path.stem in {"__init__", "base", "bootstrap", "scenario_schema_guard"}:
            continue
        tree = ast.parse(path.read_text())
        if not any(isinstance(n, ast.ImportFrom) and n.level == 1 and n.module and "." in n.module for n in tree.body):
            continue
        shims.add(path.stem)
        assert not any(isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) for n in tree.body), path
        assert all(isinstance(n, (ast.Expr, ast.ImportFrom, ast.Assign)) for n in tree.body), path
        module = importlib.import_module("app.models." + path.stem)
        assert module.__all__ and all(hasattr(module, n) for n in module.__all__), path
    for path in (ROOT / "managers").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            assert not (
                isinstance(node, ast.ImportFrom)
                and (
                    (node.level == 2 and node.module in shims)
                    or (node.level == 0 and node.module in {"app.models." + name for name in shims})
                )
            ), (
                path,
                node.lineno,
            )
