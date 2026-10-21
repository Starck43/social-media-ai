"""The admin task form must not send a string tenant_id into an integer column."""

import secrets
from types import SimpleNamespace

import pytest

from app.admin.views import AgentTaskAdmin
from app.models import AgentScenario, Source


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


@pytest.mark.asyncio
async def test_missing_target_params_reach_the_admin_toast(monkeypatch):
    """Saving a task whose scenario wants targets must not crash the form.

    Regression: the hint called `request.session.flash(...)`, but Starlette's
    Session is dict-like and has no `flash` (that is Flask), so the save died
    with `AttributeError: 'Session' object has no attribute 'flash'`. The hint
    now lands in the session key the admin layout renders as a toast.
    """

    class _ScenarioQuery:
        def filter(self, *_a, **_kw):
            return self

        async def first(self):
            return SimpleNamespace(name="Brand scenario", analysis_types=["brand_mentions"])

    monkeypatch.setattr(AgentScenario.objects, "filter", lambda *_a, **_kw: _ScenarioQuery())

    session: dict = {}
    request = SimpleNamespace(session=session)
    admin = AgentTaskAdmin()
    model = SimpleNamespace(is_active=False, job_type="analyze", cron_expr="", next_run_at=None)

    await admin.on_model_change(
        {"is_active": False, "job_type": "analyze", "agent_scenario": 7, "payload": {}},
        model,
        is_created=False,
        request=request,
    )

    assert session["admin_message"]["type"] == "warning"
    assert "укажите в параметрах задачи" in session["admin_message"]["message"]


def test_task_admin_documents_the_collection_block_and_dry_run() -> None:
    from app.admin.views import AgentTaskAdmin

    description = AgentTaskAdmin.form_args["payload"]["description"]
    for key in ("collection", "content_types", "engagement_types", "city", "candidates", "date_window"):
        assert key in description, key
    assert "типы контента задаёт сценарий" not in description
    assert AgentTaskAdmin.form_args["dry_run"]["label"] == "Тестовый прогон (dry-run)"
    assert AgentTaskAdmin.form_widget_args["payload"]["rows"] >= 6
    assert "dry_run" in AgentTaskAdmin.column_list
    assert AgentTaskAdmin.column_formatters["dry_run"](SimpleNamespace(dry_run=True), None) == "Да"
    assert AgentTaskAdmin.column_formatters["dry_run"](SimpleNamespace(dry_run=False), None) == "Нет"


def test_source_admin_marks_the_task_owned_search_lane() -> None:
    from app.admin.views import SourceAdmin

    assert SourceAdmin.column_labels["owner_task_id"] == "Поиск задачи (lane)"
    assert "owner_task_id" not in SourceAdmin.form_columns, "the task creates its own lane"
    formatter = SourceAdmin.column_formatters_detail["owner_task_id"]
    assert formatter(SimpleNamespace(owner_task_id=None), None) == "—"
    assert formatter(SimpleNamespace(owner_task_id=17), None) == "17"
    assert "SEARCH" in SourceAdmin.form_args["source_type"]["description"]


def test_collected_item_admin_exposes_selection_and_lane_fields() -> None:
    from app.admin.views import CollectedItemAdmin

    assert {"content_type", "selection_status"} <= set(CollectedItemAdmin.column_list)
    assert {"lane_id", "selection_status", "selection_reason"} <= set(CollectedItemAdmin.form_excluded_columns)
    for field in ("content_type", "parent_external_id", "lane_id", "selection_status", "selection_reason"):
        assert field in CollectedItemAdmin.column_labels, field
    assert "Родительская запись" in CollectedItemAdmin.form_args["parent_external_id"]["label"]
