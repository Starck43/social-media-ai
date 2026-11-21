"""Focused checks for canonical local tasks and the existing planner contract."""

from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models.planner import PlannerTask, Task, TaskParticipant
from app.services.planner.canonical_tasks import CreateTask, UpdateParticipation, task_service
from app.services.planner.tasks import planner_service


__source_only__ = True


def test_local_note_has_private_default_and_distinct_storage():
    note = CreateTask(title="Read the report")
    assert note.visibility == "private"
    assert note.due_at is None and note.due_date is None
    assert planner_service.model is PlannerTask
    assert task_service.model is Task
    assert Task.__table__ is not PlannerTask.__table__


def test_date_only_and_recurrence_use_planner_validation():
    note = CreateTask(title="Read the report", due_date=date(2099, 11, 21), recurrence="FREQ=DAILY")
    assert note.due_date == date(2099, 11, 21)
    assert note.due_at is None
    with pytest.raises(ValidationError):
        CreateTask(title="Read the report", recurrence="FREQ=DAILY")
    with pytest.raises(ValidationError):
        UpdateParticipation(expected_version=True, user_id=4, role="watcher", enabled=True)


@pytest.mark.parametrize("role,expected_visibilities", [("owner", {"workspace", "shared"}), ("analyst", {"workspace"})])
def test_list_visibility_correlates_collaborators_and_excludes_watchers(role, expected_visibilities):
    clause = select(Task.id).where(Task.tenant_id == 3, task_service.visibility(7, role))
    compiled = clause.compile(dialect=postgresql.dialect())
    values = set(compiled.params.values())
    assert {"collaborator", "private", *expected_visibilities} <= values
    assert ("shared" in values) == ("shared" in expected_visibilities)
    assert "watcher" not in values
    sql = str(compiled)
    for key in ("tenant_id", "task_id"):
        assert TaskParticipant.__tablename__ + "." + key in sql
