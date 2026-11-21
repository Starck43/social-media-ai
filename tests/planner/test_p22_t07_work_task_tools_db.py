"""The agent toolset and the CLI handlers act on the canonical task for real."""

import json

import pytest
import typer
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.agent.toolset import work_tasks as tool
from app.core.config import settings
from app.core.database import async_session_maker
from app.models.planner.task_run import TaskRun
from app.services.task_execution_plans import execution_plan_service
from tests.jobs.test_p22_t03_execution_plan_db import _connection
from tests.shared.execution_plan_payloads import mail_plan
from tests.support.schema_guard import require_isolated_schema

pytest_plugins = ("tests.jobs.test_p22_t03_execution_plan_db",)
pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


async def test_tools_and_cli_see_the_same_task_and_receipts(workspace, monkeypatch):
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    connection_id, pin = await _connection(workspace)
    plan = mail_plan(connection_id, pin)
    plan["steps"] = plan["steps"][:2]
    with workspace.scope():
        created = await tool.work_task_create({"title": "Synthetic tool task"})
        assert created["title"] == "Synthetic tool task"

        listed = await tool.work_task_list(limit=10, view=None)
        assert [item["id"] for item in listed["items"]] == [created["id"]]

        preview = await tool.work_task_preview(created["id"], plan)
        assert preview["task_id"] == created["id"] and preview["operations"]
        async with async_session_maker() as session:
            assert await session.scalar(select(func.count(TaskRun.id))) == 0

        await execution_plan_service.publish(created["id"], created["version"], json.dumps(plan))
        actions = await tool.work_task_actions(created["id"])
        assert "run" in {action["reference"] for action in actions["actions"]}

        receipt = await tool.work_task_run(created["id"], "tool-key")
        replay = await tool.work_task_run(created["id"], "tool-key")
        assert replay["run_id"] == receipt["run_id"] and replay["replayed"] is True
        assert replay["job_id"] == receipt["job_id"]

        runs = await tool.work_task_runs(created["id"], limit=5)
        assert [item["run_id"] for item in runs["items"]] == [receipt["run_id"]]

        stale = await tool.work_task_edit(
            created["id"], {"expected_version": 99, "scope": "occurrence", "title": "Too late"}
        )
        assert stale["code"] == "work_task_stale"

        missing = await tool.work_task_get(10**9)
        assert missing["code"] == "work_task_unavailable"


async def test_cli_command_runs_as_the_declared_member(workspace, monkeypatch, capsys):
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    from cli.commands import work_tasks as cli

    with workspace.scope():
        created = await tool.work_task_create({"title": "Synthetic CLI task"})
    cli._SCOPE.update({"tenant": str(workspace.tenants[0].id), "user": str(workspace.user.id)})
    try:
        await __import__("asyncio").to_thread(cli.show_cmd, created["id"])
    except typer.Exit as exit_code:  # a refusal would exit non-zero; success must not
        raise AssertionError(f"CLI refused a permitted read: {exit_code.exit_code}") from None
    printed = json.loads(capsys.readouterr().out)
    assert printed["id"] == created["id"] and printed["title"] == "Synthetic CLI task"
