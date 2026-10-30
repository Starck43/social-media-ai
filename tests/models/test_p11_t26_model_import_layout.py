"""Static guard for the canonical model import layout."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REMOVED_MODEL_MODULES = (
    "agent_feedback",
    "agent_memory",
    "agent_message",
    "agent_scenario",
    "agent_session",
    "agent_task",
    "ai_analytics",
    "analyzed_content_receipt",
    "bot_action",
    "collected_item",
    "digest_run",
    "job",
    "llm_attempt",
    "llm_budget",
    "llm_dispatch_receipt",
    "llm_model",
    "llm_outcome_event",
    "llm_provider",
    "model_type",
    "notification",
    "permission",
    "platform",
    "role",
    "source",
    "tenant",
    "user",
    "user_credential",
    "analysis/llm_attempt",
    "analysis/llm_budget",
    "analysis/llm_dispatch_receipt",
    "analysis/llm_model",
    "analysis/llm_outcome_event",
    "analysis/llm_provider",
    "scheduling/bot_action",
    "scheduling/digest_run",
)


def test_removed_compatibility_modules_are_absent():
    for module in REMOVED_MODEL_MODULES:
        assert not (ROOT / "app/models" / f"{module}.py").exists(), module


def test_analysis_and_scheduling_exports_stay_domain_scoped():
    packages = {
        "analysis": {
            "LLMAttempt",
            "LLMBudgetDay",
            "LLMBudgetReservation",
            "LLMDispatchReceipt",
            "LLMModel",
            "LLMOutcomeEvent",
            "LLMProvider",
        },
        "scheduling": {"BotAction", "DigestRun"},
    }
    for package, forbidden in packages.items():
        tree = ast.parse((ROOT / f"app/models/{package}/__init__.py").read_text(encoding="utf-8"))
        exports = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
        )
        assert not forbidden.intersection(exports), package
