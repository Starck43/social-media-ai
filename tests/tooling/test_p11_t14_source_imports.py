"""Stdlib-only guard for the canonical model import boundary."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REMOVED_MODEL_MODULES = {
    "app.models.agent_feedback",
    "app.models.agent_memory",
    "app.models.agent_message",
    "app.models.agent_scenario",
    "app.models.agent_session",
    "app.models.agent_task",
    "app.models.ai_analytics",
    "app.models.analyzed_content_receipt",
    "app.models.bot_action",
    "app.models.collected_item",
    "app.models.digest_run",
    "app.models.job",
    "app.models.llm_attempt",
    "app.models.llm_budget",
    "app.models.llm_dispatch_receipt",
    "app.models.llm_model",
    "app.models.llm_outcome_event",
    "app.models.llm_provider",
    "app.models.model_type",
    "app.models.notification",
    "app.models.permission",
    "app.models.platform",
    "app.models.role",
    "app.models.source",
    "app.models.tenant",
    "app.models.user",
    "app.models.user_credential",
    "app.models.analysis.llm_attempt",
    "app.models.analysis.llm_budget",
    "app.models.analysis.llm_dispatch_receipt",
    "app.models.analysis.llm_model",
    "app.models.analysis.llm_outcome_event",
    "app.models.analysis.llm_provider",
    "app.models.scheduling.bot_action",
    "app.models.scheduling.digest_run",
}


def imported_modules(path: Path, source: str) -> set[str]:
    package = ".".join(path.parent.parts)
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = importlib.util.resolve_name("." * node.level + module, package)
            modules.add(module)
        elif isinstance(node, ast.Call) and node.args:
            function = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if function in {"import_module", "__import__"} and isinstance(node.args[0], ast.Constant):
                if isinstance(node.args[0].value, str):
                    modules.add(node.args[0].value)
    return modules


def test_first_party_code_does_not_import_removed_model_modules():
    failures = []
    for top in ("app", "cli", "scripts", "migrations", "tests"):
        for path in (ROOT / top).rglob("*.py"):
            relative = path.relative_to(ROOT)
            for module in imported_modules(relative, path.read_text(encoding="utf-8")):
                if any(module == removed or module.startswith(removed + ".") for removed in REMOVED_MODEL_MODULES):
                    failures.append(f"{relative}:{module}")
    assert failures == []
