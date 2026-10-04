"""The admin task form must not send a string tenant_id into an integer column."""

import secrets
from types import SimpleNamespace

import pytest

from app.admin.views import AgentTaskAdmin
from app.models import Source


class _TenantRow:
    id = 1


class _FakeQuery:
    """Stands in for the Queryset chain `filter(...).values(...).rows()`."""

    def __init__(self, rows):
        self._rows = rows
        self.kwargs: dict = {}

    def values(self, *_a, **_kw):
        return self

    async def rows(self):
        return self._rows


def _capture_filter(monkeypatch, rows):
    """Patch `Source.objects.filter` and record what the view passed it."""
    captured: dict = {}

    def fake_filter(**kwargs):
        captured.update(kwargs)
        return _FakeQuery(rows)

    monkeypatch.setattr(Source.objects, "filter", fake_filter)
    return captured


@pytest.mark.asyncio
async def test_string_workspace_from_the_form_is_coerced_to_int(monkeypatch):
    """Regression: `operator does not exist: integer = character varying`.

    The admin form hands the workspace over as the string "1" (it is a form
    field), and it was compared straight against `sources.tenant_id`. The write
    failed with a ProgrammingError, so the operator saw an opaque error instead
    of the task being saved and the guard never reached its real job.
    """
    admin = AgentTaskAdmin()
    captured = _capture_filter(monkeypatch, [(1,)])

    model = SimpleNamespace(is_active=False, job_type="analyze", cron_expr="", next_run_at=None)
    await admin.on_model_change({"tenant": "1", "is_active": True, "job_type": "analyze"}, model, is_created=False)

    assert "tenant_id" in captured
    assert captured["tenant_id"] == 1
    assert isinstance(captured["tenant_id"], int)


@pytest.mark.asyncio
async def test_an_object_workspace_still_works(monkeypatch):
    """A related-object workspace (the normal form path) keeps working."""
    admin = AgentTaskAdmin()
    captured = _capture_filter(monkeypatch, [(1,)])

    model = SimpleNamespace(is_active=False, job_type="analyze", cron_expr="", next_run_at=None)
    await admin.on_model_change(
        {"tenant": _TenantRow(), "is_active": True, "job_type": "analyze"}, model, is_created=False
    )

    assert captured["tenant_id"] == 1


@pytest.mark.asyncio
async def test_a_non_numeric_workspace_does_not_crash(monkeypatch):
    """A garbage value must not reach SQL as a tenant filter."""
    admin = AgentTaskAdmin()
    _capture_filter(monkeypatch, [])

    model = SimpleNamespace(is_active=False, job_type="analyze", cron_expr="", next_run_at=None)
    # No ProgrammingError: a non-numeric workspace matches nothing, so the
    # guard rejects the activation instead of letting the DB raise.
    with pytest.raises(ValueError):
        await admin.on_model_change(
            {"tenant": "not-a-number", "is_active": True, "job_type": "analyze"}, model, is_created=False
        )


@pytest.mark.asyncio
async def test_an_active_collect_task_saves_through_the_real_form(monkeypatch):
    """The full save path against the database, which is where the error showed.

    The unit tests above pin the value handed to the filter; this one goes
    through `on_model_change` with a real `AgentTask` and a real workspace, so
    the SQL actually runs against `sources.tenant_id`.
    """
    from app.core.tenant_context import tenant_scope
    from app.models import AgentTask, Platform, Tenant
    from app.types import SourceType

    with tenant_scope(bypass=True):
        tenant = await Tenant.objects.create(name=f"admin-form-{secrets.token_hex(4)}", slug=f"af-{secrets.token_hex(4)}")
        platform = await Platform.objects.filter(is_active=True).first()
        source = await Source.objects.create(
            tenant_id=tenant.id,
            platform_id=platform.id,
            name=f"af-src-{secrets.token_hex(4)}",
            external_id=f"af-ext-{secrets.token_hex(4)}",
            source_type=SourceType.USER,
            is_active=True,
            params={},
        )
    task_id = None
    try:
        admin = AgentTaskAdmin()
        with tenant_scope(bypass=True):
            task = await AgentTask.objects.create(
                tenant_id=tenant.id,
                name=f"af-task-{secrets.token_hex(4)}",
                job_type="collect",
                cron_expr="0 4 * * *",
                payload={},
                is_active=True,
            )
        task_id = task.id

        # What the form does: the workspace arrives as the string "1"-style
        # form value, not as an int.
        await admin.on_model_change(
            {
                "tenant": str(tenant.id),
                "is_active": True,
                "job_type": "collect",
                "cron_expr": "0 4 * * *",
                "sources": [str(source.id)],
            },
            task,
            is_created=False,
        )

        with tenant_scope(bypass=True):
            saved = await AgentTask.objects.get(id=task.id)
        assert saved.tenant_id == tenant.id
    finally:
        if task_id is not None:
            with tenant_scope(bypass=True):
                await AgentTask.objects.delete_by_id(task_id)
                await Source.objects.delete_by_id(source.id)
                await Tenant.objects.delete_by_id(tenant.id)
