"""No-DB contracts for web and API repeat-policy boundaries."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _function(path: Path, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse(path.read_text())
    return next(
        node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name
    )


@pytest.mark.asyncio
async def test_web_queue_explicitly_disables_force_flags_for_legacy_task():
    function = _function(ROOT / "app/web/tasks.py", "queue_task_now")
    function.decorator_list = []
    function.body = [node for node in function.body if not isinstance(node, ast.ImportFrom)]
    enqueue = AsyncMock(return_value=NS(id=12, status="pending"))
    scope = {
        "enqueue_task_run": enqueue,
        "SourceJobBusy": RuntimeError,
        "launch_job_background": lambda job_id, **kwargs: None,
    }
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(module, "isolated_web_queue", "exec"), scope)

    task = NS(id=7, payload={"force_refresh": True, "force_reanalyze": True})
    result = await scope["queue_task_now"](task, initiated_by_user_id=3)

    assert result == {"job_id": 12, "status": "pending"}
    assert enqueue.await_args.kwargs["extra_payload"] == {"force_refresh": False, "force_reanalyze": False}


def test_api_payload_scrubber_removes_repeat_bypass_keys_only():
    function = _function(ROOT / "app/api/v1/endpoints/tasks.py", "_web_api_payload")
    namespace: dict = {}
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
            "isolated_api_payload",
            "exec",
        ),
        namespace,
    )

    payload = {"force_refresh": True, "force_reanalyze": True, "analyze_inline": True}
    assert namespace["_web_api_payload"](payload) == {"analyze_inline": True}
    assert payload["force_refresh"] is True


@pytest.mark.asyncio
async def test_api_task_run_uses_only_false_force_flags_as_one_off_overrides():
    function = _function(ROOT / "app/api/v1/endpoints/tasks.py", "run_task")
    function.decorator_list = []
    function.args.defaults = [ast.Constant(None) for _ in function.args.defaults]
    function.body = [node for node in function.body if not isinstance(node, ast.ImportFrom)]
    enqueue = AsyncMock(return_value=NS(id=15))
    run = AsyncMock(return_value={"status": "done"})
    namespace = {
        "_web_api_payload": lambda payload: {
            key: value
            for key, value in (payload or {}).items()
            if key not in {"force_refresh", "force_reanalyze"}
        },
        "HTTPException": RuntimeError,
        "SourceJobBusy": RuntimeError,
        "AgentTask": NS(objects=NS(get=AsyncMock(return_value=NS(payload={"force_refresh": True})))),
        "enqueue_task_run": enqueue,
        "run_job_now": run,
    }
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[future, function], type_ignores=[])),
            "isolated_api_run",
            "exec",
        ),
        namespace,
    )

    await namespace["run_task"](9, request=NS(payload={"future_flag": "submitted"}), _user=NS(id=4))

    assert enqueue.await_args.kwargs["extra_payload"] == {
        "_one_off": True,
        "force_refresh": False,
        "force_reanalyze": False,
    }
