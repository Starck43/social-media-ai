"""No-DB safety policy checks for Job retention and replay."""

import ast
import asyncio
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.jobs.lifecycle import (
    DEFAULT_FAILURE_HOURS,
    DEFAULT_SUCCESS_HOURS,
    _retention_delete_criteria,
    retention_cutoff,
    retention_policy,
)
from app.jobs.replay_policy import can_replay, replay_block_reason


def _job(**overrides):
    values = {
        "status": "failed",
        "job_type": "collect",
        "error": "ordinary handler failure",
        "result": {},
        "payload": {
            "_effective_snapshot": {
                "version": 1,
                "task_payload": {
                    "source_ids": [4],
                    "selection": {"brands": ["x"]},
                    "cli_dates": {"start_date": "2026-01-01", "end_date": "2026-02-01"},
                },
            }
        },
        "agent_task_id": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_retention_defaults_and_valid_override():
    assert (DEFAULT_SUCCESS_HOURS, DEFAULT_FAILURE_HOURS) == (24, 24)
    assert retention_policy({"mode": "job_history"}) == (24, 24)
    assert retention_policy({"mode": "job_history", "success_hours": 48, "failure_hours": 72}) == (48, 72)
    assert retention_policy({"mode": "legacy", "success_hours": 72}) is None
    assert retention_policy({"mode": "job_history", "success_hours": True, "failure_hours": 24}) is None
    assert retention_policy({"mode": "job_history", "success_hours": 8761, "failure_hours": 24}) is None
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    configured = retention_policy({"mode": "job_history", "success_hours": 48, "failure_hours": 72})
    assert retention_cutoff(now, "done", configured) == now - timedelta(hours=48)
    assert retention_cutoff(now, "failed", configured) == now - timedelta(hours=72)


def test_automatic_delete_sql_is_tenant_terminal_and_review_scoped():
    from app.jobs.lifecycle import Job

    cutoff = datetime(2026, 10, 18, tzinfo=timezone.utc)
    sql = str(
        select(Job.id)
        .where(*_retention_delete_criteria(tenant_id=17, status="failed", cutoff=cutoff))
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert "tenant_id" in sql and "finished_at" in sql and "status" in sql
    assert "outcome unconfirmed" in sql
    assert "partial_delivery_requires_review" in sql
    assert "digest_run_in_progress_requires_review" in sql


def test_web_attention_query_is_text_only_and_active_history_filters_match():
    from app.models.scheduling.job import Job
    from app.models.managers.job_manager import web_attention_criteria, web_page_status_criteria

    attention_sql = str(
        select(Job.id)
        .where(web_attention_criteria(Job))
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    active_sql = str(
        select(Job.id)
        .where(web_page_status_criteria(Job, ["pending", "running", "failed"], True))
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    history_sql = str(
        select(Job.id)
        .where(web_page_status_criteria(Job, ["done"], False))
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    for key in (
        "status",
        "error",
        "errors",
        "staged_errors",
        "rate_limited",
        "collection_errors",
        "collection_rate_limited",
        "review_required",
    ):
        assert key in attention_sql
    assert "partial_delivery_requires_review" in attention_sql
    assert "digest_run_in_progress_requires_review" in attention_sql
    assert "::integer" not in attention_sql and "::numeric" not in attention_sql and "::boolean" not in attention_sql
    assert "as integer" not in attention_sql and "as numeric" not in attention_sql and "as boolean" not in attention_sql
    assert "status" in active_sql and "done" in active_sql and " or " in active_sql
    assert "done" in history_sql and "not" in history_sql and "review_required" in history_sql


def test_done_job_attention_projection_is_conservative_for_malformed_values():
    from app.web.jobs import _job_has_attention

    assert not _job_has_attention(_job(status="failed", result={"staged_errors": 9}))
    assert _job_has_attention(_job(status="done", result={"staged_errors": 2}))
    assert _job_has_attention(_job(status="done", result={"error": True}))
    assert _job_has_attention(_job(status="done", result={"rate_limited": 1.5}))
    assert _job_has_attention(_job(status="done", result={"review_required": "yes"}))
    assert _job_has_attention(_job(status="done", result={"errors": {"unexpected": "shape"}}))


async def test_web_maintenance_cleanup_uses_trusted_bypass_and_cancels_cleanly():
    source = (Path(__file__).resolve().parents[2] / "app/main.py").read_text()
    tree = ast.parse(source)
    function = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_job_maintenance_loop"
    )
    bypass = ContextVar("test_web_maintenance_bypass", default=False)
    calls = []

    @contextmanager
    def tenant_scope(*, bypass=False):
        context_token = test_bypass.set(bypass)
        try:
            yield
        finally:
            test_bypass.reset(context_token)

    test_bypass = bypass

    async def cleanup():
        calls.append(bypass.get())
        assert bypass.get() is True

    async def reap():
        return 0

    async def stop_after_first_cycle(_seconds):
        raise asyncio.CancelledError

    lifecycle = ModuleType("app.jobs.lifecycle")
    lifecycle.cleanup_terminal_job_history = cleanup
    lifecycle.reap_stale_jobs = reap
    namespace = {
        "asyncio": SimpleNamespace(CancelledError=asyncio.CancelledError, sleep=stop_after_first_cycle),
        "logger": SimpleNamespace(warning=lambda *args, **kwargs: None),
        "tenant_scope": tenant_scope,
    }
    with patch.dict(sys.modules, {"app.jobs.lifecycle": lifecycle}):
        exec(
            compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), "app/main.py", "exec"),
            namespace,
        )
        try:
            await namespace["_job_maintenance_loop"]()
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("maintenance loop must propagate cancellation")
    assert calls == [True]
    assert bypass.get() is False


def test_replay_requires_complete_safe_source_snapshot():
    assert can_replay(_job())
    assert not can_replay(_job(status="running"))
    assert not can_replay(_job(job_type="digest"))
    assert not can_replay(_job(error="Queue outcome unconfirmed; inspect before retry. timeout"))
    assert not can_replay(_job(result={"review_required": True}))
    assert not can_replay(_job(result={"diagnostic_code": "partial_delivery_requires_review"}))
    assert not can_replay(_job(payload={"_effective_snapshot": {"version": 1, "task_payload": {"source_ids": []}}}))
    assert "снимка" in replay_block_reason(
        _job(payload={"_effective_snapshot": {"version": 1, "task_payload": {"source_ids": []}}})
    )
    assert not can_replay(
        _job(
            payload={
                "_effective_snapshot": {"version": 1, "task_payload": {"source_ids": [4]}, "action_type": "publish"}
            }
        )
    )
    assert not can_replay(
        _job(
            payload={
                "_effective_snapshot": {
                    "version": 1,
                    "task_payload": {"source_ids": [4], "cli_dates": {"start_date": "nonsense"}},
                }
            }
        )
    )
    assert not can_replay(
        _job(
            payload={
                "_effective_snapshot": {
                    "version": 1,
                    "task_payload": {"source_ids": [4], "cli_dates": {"start_date": "2026-01-01"}},
                }
            }
        )
    )


def test_retention_task_manager_requires_job_update_permission():
    from app.core.permissions import PermissionDeniedError
    from app.models.managers.agent_task_manager import AgentTaskManager

    with (
        patch("app.core.permissions.get_current_user", return_value=object()),
        patch("app.core.permissions.has_permission", return_value=False),
    ):
        try:
            AgentTaskManager._require_job_retention_permission("prune")
        except PermissionDeniedError:
            pass
        else:
            raise AssertionError("A task manager without job.update changed retention")
    with (
        patch("app.core.permissions.get_current_user", return_value=object()),
        patch("app.core.permissions.has_permission", return_value=True),
    ):
        AgentTaskManager._require_job_retention_permission("prune")


def test_history_retention_policy_requires_recurring_schedule_but_legacy_prune_does_not():
    from app.models.managers.agent_task_manager import AgentTaskManager

    try:
        AgentTaskManager._validate_job_retention_schedule(
            "prune", {"mode": "job_history", "success_hours": 48, "failure_hours": 72}, "@once"
        )
    except ValueError as error:
        assert "повторяемой" in str(error)
    else:
        raise AssertionError("A one-time policy task would deactivate and silently restore the default")
    AgentTaskManager._validate_job_retention_schedule("prune", {"mode": "legacy"}, "@once")
    AgentTaskManager._validate_job_retention_schedule("prune", {"mode": "job_history"}, "0 9 * * *")


def test_web_prune_payload_roundtrip_and_history_clear_guards():
    from app.web.jobs import _safe_terminal_delete_criteria
    from app.web.tasks import _payload_from_form

    payload = _payload_from_form("prune", "ignored", "", "", retention_success_hours=72, retention_failure_hours=96)
    assert payload == {"mode": "job_history", "success_hours": 72, "failure_hours": 96}
    from app.models.scheduling.job import Job

    sql = str(
        select(Job.id)
        .where(*_safe_terminal_delete_criteria())
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert "needs_review" in sql and "review_required" in sql
    assert "partial_delivery_requires_review" in sql
    assert "digest_run_in_progress_requires_review" in sql
