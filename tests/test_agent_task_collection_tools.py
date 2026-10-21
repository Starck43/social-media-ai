"""Chat task tools expose the same task-owned collection intent.

The chat route matters for parity: a block created there must not be a different
shape, and the confirmation must let the owner judge what will be collected
before approving the write.
"""

from __future__ import annotations

import inspect

from app.agent import runtime
from app.agent.toolset import tasks
from app.agent.tools import tool_specs


def test_task_add_and_update_accept_collection() -> None:
    specs = {spec["function"]["name"]: spec["function"] for spec in tool_specs()}
    for name in ("task_add", "task_update"):
        function = getattr(tasks, name)
        assert "collection" in inspect.signature(function).parameters, name
        assert "collection" in specs[name]["parameters"]["properties"], name
        assert "content_types" in specs[name]["parameters"]["properties"]["collection"]["description"]


def test_task_tools_go_through_the_shared_writer() -> None:
    for function in (tasks.task_add, tasks.task_update):
        source = inspect.getsource(function)
        assert "apply_collection" in source, function.__name__
        assert "collection_error_message" in source, function.__name__


def test_task_add_confirmation_summarises_the_collection() -> None:
    preview = runtime._human_confirmation(
        "task_add",
        {
            "name": "Мониторинг",
            "job_type": "analyze",
            "cron": "0 9 * * *",
            "start_date": "2026-10-01",
            "collection": {
                "content_types": ["posts", "comments"],
                "limit": 250,
                "queries": ["доставка"],
                "city": {"name": "Киров"},
            },
        },
    )
    assert "сбор:" in preview
    assert "записи + комментарии" in preview
    assert "лимит 250" in preview
    assert "город Киров" in preview
    assert "доставка" in preview


def test_task_update_confirmation_names_the_collection_change() -> None:
    preview = runtime._human_confirmation("task_update", {"name": "Мониторинг", "collection": {"limit": 10}})
    assert "сбор" in preview
