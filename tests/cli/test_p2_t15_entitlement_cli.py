"""P2-T15 CLI entitlement previews stay read-only and workspace-scoped."""

import asyncio
import json
from unittest.mock import AsyncMock

from typer.testing import CliRunner

from app.core.tenant_context import current_tenant_id
from cli.commands import tasks as task_commands
from cli.main import app

__source_only__ = True


def _plan_json():
    return json.dumps(
        {
            "goal": "Research VK",
            "platforms": ["vk"],
            "source_scope": "all",
            "collection": {"queries": ["repair"]},
        }
    )


def test_offline_plan_preview_explicitly_marks_entitlements_unchecked(monkeypatch):
    monkeypatch.setattr(task_commands, "_require_operator_context", lambda: None)

    result = CliRunner().invoke(app, ["task", "plan", _plan_json()])

    assert result.exit_code == 0, result.output
    assert '"allowed": null' in result.output
    assert '"reason": "workspace_required"' in result.output
    assert '"checked": false' in result.output


def test_live_plan_preview_resolves_tenant_scope_and_renders_decision(monkeypatch):
    monkeypatch.setattr(task_commands, "_require_operator_context", lambda: None)
    monkeypatch.setattr(task_commands, "_run_task_platform", asyncio.run)
    monkeypatch.setattr(task_commands, "_require_explicit_tenant", AsyncMock(return_value=7))

    async def preview(_payload):
        assert current_tenant_id() == 7
        return {
            "allowed": False,
            "required": ["connector.vk.search"],
            "denials": [{"capability": "connector.vk.search", "reason": "plan_expired"}],
        }

    monkeypatch.setattr("app.services.collection.entitlement_policy.preview_entitlements", preview)
    monkeypatch.setattr(
        "app.services.social.vk_task_access.preview_vk_access",
        AsyncMock(return_value={"checked": False, "reason": "legacy_source_access"}),
    )

    result = CliRunner().invoke(app, ["task", "plan", _plan_json(), "--tenant", "workspace"])

    assert result.exit_code == 0, result.output
    assert '"required": [' in result.output
    assert '"connector.vk.search"' in result.output
    assert '"reason": "plan_expired"' in result.output


def test_entitlements_command_inspects_without_consuming_usage(monkeypatch):
    monkeypatch.setattr(task_commands, "_require_operator_context", lambda: None)
    monkeypatch.setattr(task_commands, "_run_task_platform", asyncio.run)
    monkeypatch.setattr(task_commands, "_require_explicit_tenant", AsyncMock(return_value=7))
    inspect = AsyncMock(
        return_value={
            "plan": "pro",
            "catalog_version": 1,
            "decision": {"allowed": True, "required": ["connector.vk.read"], "denials": []},
        }
    )

    def manager():
        assert current_tenant_id() == 7
        return type("Manager", (), {"inspect": inspect})()

    monkeypatch.setattr("app.models.managers.entitlement_manager.EntitlementManager", manager)

    result = CliRunner().invoke(app, ["task", "entitlements", "--tenant", "workspace"])

    assert result.exit_code == 0, result.output
    assert '"catalog_version": 1' in result.output
    assert '"connector.vk.read"' in result.output
    inspect.assert_awaited_once_with()
