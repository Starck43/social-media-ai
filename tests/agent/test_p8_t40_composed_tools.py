"""Confirmation and permission boundary for composed task digest sending."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

__source_only__ = True


def test_digest_send_tool_is_confirmed_and_requires_digest_create_permission():
    from app.agent.tools import get_tool
    from app.agent.toolset import tasks

    send = get_tool("task_digest_send")
    assert send is not None
    assert send.handler is tasks.task_digest_send
    assert send.confirm is True
    assert send.required_permission == "notifications.digest_run.create"
    assert send.parameters["required"] == ["task_id", "job_id"]
    assert send.parameters["additionalProperties"] is False
    assert get_tool("task_add").confirm is True


async def test_digest_send_delegates_only_the_selected_saved_run(monkeypatch):
    from app.agent.toolset import tasks

    calls = []
    module = ModuleType("app.services.task_digest_delivery")

    async def send_task_digest(task_id, job_id):
        calls.append((task_id, job_id))
        return {"status": "sent", "sent_parts": 2}

    module.send_task_digest = send_task_digest
    monkeypatch.setitem(sys.modules, "app.services.task_digest_delivery", module)
    assert await tasks.task_digest_send(31, 42) == {"status": "sent", "sent_parts": 2}
    assert calls == [(31, 42)]


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (PermissionError("foreign workspace"), {"error": "Report delivery is not permitted.", "code": "task_digest_forbidden"}),
        (RuntimeError("secret destination"), {"error": "Inspect the retained delivery receipts before retrying.", "code": "task_digest_requires_review"}),
    ],
)
async def test_digest_send_hides_permission_and_delivery_error_details(monkeypatch, failure, expected):
    from app.agent.toolset import tasks

    module = ModuleType("app.services.task_digest_delivery")

    async def fail(*_args, **_kwargs):
        raise failure

    module.send_task_digest = fail
    monkeypatch.setitem(sys.modules, "app.services.task_digest_delivery", module)
    assert await tasks.task_digest_send(31, 42) == expected
    assert "secret destination" not in repr(expected)
