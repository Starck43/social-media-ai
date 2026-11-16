"""Filtered task deletion permission regressions.

The storage below the gate is mocked: no deletion SQL is issued. What is pinned
is the gate itself — `AgentTaskManager.delete(**filters)` asks for
`agenttask.delete` before it reads anything, and re-asks before every attempt.

Since the queued-run cleanup landed, the filtered delete no longer calls
`BaseManager.delete`: it reads the matching rows through the *tenant-scoped*
queryset, re-locks them, re-checks the job-retention right per row, deletes the
pending jobs and the rows on its own session. The tenant read, the admission
lock and the per-row job cleanup are the seams doubled here; the gate and the
retention re-check run for real.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.permissions import PermissionDeniedError, permission_scope
from app.core.tenant_context import tenant_scope
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.job_manager import JobManager
from app.types import ActionType

pytestmark = pytest.mark.tenancy


def actor(allowed):
    # A platform operator running the arrange bypass. Without the membership
    # preset `member_decision` abstains, and `has_permission` answers a
    # *workspace* request only from the membership — so the bypass context is
    # what these cases need, not a tenant-only one.
    return SimpleNamespace(
        is_active=True,
        has_admin_access=lambda: True,
        has_perm_for=lambda model, action: allowed and model == "agenttask" and action == ActionType.DELETE,
    )


class _Task:
    """One matching row, as the read and the locked re-read hand it back."""

    def __init__(self, task_id: int, tenant_id: int = 31, job_type: str = "digest") -> None:
        self.id = task_id
        self.tenant_id = tenant_id
        self.job_type = job_type


class _QuerySet:
    """The chained reads `_delete_with_queued_cleanup` issues, answered from `rows`."""

    def __init__(self, rows) -> None:
        self._rows = rows

    def filter(self, *args, **kwargs):
        return _QuerySet(self._rows)

    def to_select(self):
        return self

    def order_by(self, *args):
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def execution_options(self, **kwargs):
        return self

    async def all(self):
        return self._rows


class _Session:
    def __init__(self, rows=()) -> None:
        self.rows = list(rows)
        self.deleted = []
        self.flushed = 0

    async def execute(self, *args, **kwargs):
        # `(await session.execute(...)).scalars().all()` — plain sync result.
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.rows))

    async def delete(self, row) -> None:
        self.deleted.append(row)

    async def flush(self) -> None:
        self.flushed += 1


def _manager(rows=()):
    """A manager whose tenant read is doubled."""
    manager = object.__new__(AgentTaskManager)
    manager._tenant_scoped = lambda: True
    manager._require_job_retention_permission = lambda job_type: None
    manager.get_queryset = lambda session: _QuerySet(list(rows))
    return manager


def _cleanup():
    """`JobManager.delete_pending_for_task` doubled: one call per matched row."""
    return patch.object(JobManager, "delete_pending_for_task", AsyncMock())


def _admission():
    """The admission lock only serialises writers; it is not under test here."""
    return patch.object(JobManager, "lock_source_admission", AsyncMock())


@pytest.mark.parametrize("filters", [{"name": "private-task"}, {"id": 91}, {}], ids=["name", "id", "all"])
async def test_filtered_delete_refuses_actor_without_delete_before_storage(filters):
    manager = _manager(rows=[_Task(91)])
    session = _Session(rows=[_Task(91)])
    read = AsyncMock()
    with patch.object(AgentTaskManager, "get_queryset", read):
        with tenant_scope(31), permission_scope(actor(False)):
            with pytest.raises(PermissionDeniedError):
                await manager.delete(session=session, **filters)
    read.assert_not_called()
    assert session.deleted == []


@pytest.mark.parametrize("filters", [{"name": "private-task"}, {"id": 91}, {}], ids=["name", "id", "all"])
async def test_filtered_delete_preserves_allowed_filters_and_count(filters):
    manager = _manager(rows=[_Task(91)])
    session = _Session(rows=[_Task(91)])
    with _admission(), _cleanup() as cleanup:
        with tenant_scope(bypass=True), permission_scope(actor(True)):
            result = await manager.delete(session=session, **filters)
    assert result == 1
    assert [row.id for row in session.deleted] == [91]
    assert session.flushed == 1
    cleanup.assert_awaited_once()


async def test_filtered_delete_deletes_nothing_when_no_row_matches():
    manager = _manager()
    session = _Session()
    with _admission(), _cleanup() as cleanup:
        with tenant_scope(bypass=True), permission_scope(actor(True)):
            assert await manager.delete(session=session, name="missing") == 0
    assert session.deleted == []
    cleanup.assert_not_awaited()


async def test_filtered_delete_keeps_explicit_operator_bypass():
    """No actor at all: only the trusted bootstrap entrypoint may omit one."""
    manager = _manager(rows=[_Task(92)])
    session = _Session(rows=[_Task(92)])
    with _admission(), _cleanup():
        with tenant_scope(bypass=True):
            assert await manager.delete(session=session, name="operator-task") == 1
    assert [row.id for row in session.deleted] == [92]


async def test_filtered_delete_rechecks_right_before_each_attempt():
    """Two attempts; the second one is refused before it reaches storage."""
    manager = _manager(rows=[_Task(91)])
    session = _Session(rows=[_Task(91)])
    user = actor(True)
    with _admission(), _cleanup():
        with tenant_scope(bypass=True), permission_scope(user):
            assert await manager.delete(session=session, id=91) == 1
            user.has_perm_for = lambda *args: False
            with pytest.raises(PermissionDeniedError):
                await manager.delete(session=session, id=91)
    assert len(session.deleted) == 1
    assert session.flushed == 1
