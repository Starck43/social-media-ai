"""CLI reply surface: creational body per call, no scheduling path (P15-T08)."""

import json
from unittest.mock import AsyncMock

from typer.testing import CliRunner

from cli.commands import communications as cli

runner = CliRunner()


def test_platform_runs_a_fresh_coroutine_each_invocation(monkeypatch):
    calls = []

    async def body():
        calls.append(1)
        return {"ok": True}

    assert cli._platform(body) == {"ok": True}
    assert cli._platform(body) == {"ok": True}
    assert len(calls) == 2


def test_reply_preview_prints_verified_origin_and_writes_nothing(monkeypatch):
    from app.agent.toolset import communications as tool

    preview = AsyncMock(return_value={"thread_id": 3, "inbound_message_id": 4, "writes": False})
    monkeypatch.setattr(tool, "reply_preview", preview)
    result = runner.invoke(cli.app, ["reply-preview", "3", "4"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["writes"] is False
    preview.assert_awaited_once_with(3, 4)


def test_reply_draft_dry_run_changes_nothing(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr("app.services.communications.reply.create_reply_draft", create)
    result = runner.invoke(
        cli.app,
        ["reply-draft", "3", "4", "-b", "Thanks", "--idempotency-key", "k1", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["writes"] is False
    create.assert_not_awaited()


def test_reply_draft_requires_an_idempotency_key():
    result = runner.invoke(cli.app, ["reply-draft", "3", "4", "-b", "Thanks"])
    assert result.exit_code != 0


def test_reply_surface_has_no_send_or_schedule_command():
    commands = {command.name for command in cli.app.registered_commands}
    assert commands == {"reply-preview", "reply-draft"}
    assert not commands & {"send", "schedule", "dispatch"}


def test_cli_refusal_is_a_failure_not_a_silent_success(monkeypatch):
    from app.agent.toolset import communications as tool

    monkeypatch.setattr(tool, "reply_preview", AsyncMock(side_effect=PermissionError("reply_denied")))
    result = runner.invoke(cli.app, ["reply-preview", "3", "4"])
    assert result.exit_code != 0
    assert isinstance(result.exception, PermissionError)
