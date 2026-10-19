"""Task diagnostics cannot borrow model calls from another task, Job or tenant."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.models.managers.llm_attempt_manager import LLMAttemptManager


@pytest.mark.asyncio
async def test_task_run_journal_queries_require_task_job_and_tenant(monkeypatch):
    manager = LLMAttemptManager()
    statements = []

    class Session:
        async def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(
                one=lambda: SimpleNamespace(total=3, succeeded=2),
                first=lambda: SimpleNamespace(model_id="model-a", provider_name="provider-a", outcome="failed_output"),
            )

    @asynccontextmanager
    async def owned():
        yield Session()

    monkeypatch.setattr(manager, "_get_owned_session", owned)
    monkeypatch.setattr(manager, "_tenant_criterion", lambda: [manager.model.tenant_id == 7])

    result = await manager.task_run_summary(21, 31)

    assert result["succeeded"] == 2
    assert result["failed"] == 1
    assert result["model"] == "model-a"
    assert result["error_code"] == "failed_output"
    assert len(statements) == 2
    for statement in statements:
        bound = statement.compile().params
        assert bound["agent_task_id_1"] == 21
        assert bound["job_id_1"] == 31
        assert bound["tenant_id_1"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("task_id,job_id", [(True, 31), (21, False), (0, 31), (21, -1), ("21", 31)])
async def test_task_run_journal_rejects_missing_scope_before_reading(task_id, job_id, monkeypatch):
    manager = LLMAttemptManager()

    def no_session():
        raise AssertionError("Invalid scope reached storage")

    monkeypatch.setattr(manager, "_get_owned_session", no_session)
    with pytest.raises(ValueError):
        await manager.task_run_summary(task_id, job_id)
