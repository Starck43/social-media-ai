"""Synthetic form and actual SQLAdmin route checks; no project DB imports."""

import re
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import pytest
from shared.filtered_select_fixture import FilteredSelectMultipleField, source_configuration, task_console
from sqladmin.editors import collect_form_media
from starlette.datastructures import FormData
from wtforms import Form

ROOT = Path(__file__).resolve().parents[2]


def make_form(**kwargs):
    class Choices(Form):
        sources = FilteredSelectMultipleField(data=[("1", "Первый"), ("2", "Второй")], **kwargs)
        mirror = FilteredSelectMultipleField(data=[("3", "Третий")])

    return Choices


def test_shared_field_preserves_bound_ids_and_deduplicates_assets():
    form = make_form()(obj=NS(sources=[NS(id=2)], mirror=[]))
    # Use actual mapped instances for SQLAdmin PK inspection.
    from shared.filtered_select_fixture import Source

    form.sources.data = [Source(id=2)]
    html = str(form.sources())
    assert "multiple" in html and 'data-filtered-select="plain"' in html
    assert re.findall(r'<option selected value="([^"]+)"', html) == ["2"]
    assert 'name="sources"' in html and "hidden" not in html
    assert 'size="10"' in html
    media = collect_form_media(form)
    assert media.css == ["/static/css/permission-transfer.css"]
    assert media.js == ["/static/js/permission-transfer.js"]


@pytest.mark.parametrize(
    "submitted,valid,expected",
    [
        ([], True, []),
        (["1"], True, ["1"]),
        (["1", "2"], True, ["1", "2"]),
        (["foreign-999"], False, None),
        (["1", "foreign-999"], False, None),
    ],
)
def test_native_post_validation_rejects_unoffered_ids(submitted, valid, expected):
    form = make_form()(FormData([("sources", x) for x in submitted]))
    assert form.validate() is valid
    if valid:
        assert sorted(form.sources.data or []) == expected


def test_modes_and_disabled_fallback():
    form = make_form(transfer_mode="permissions", render_kw={"disabled": True})()
    html = str(form.sources())
    assert 'data-filtered-select="permissions"' in html and 'data-permission-transfer="true"' in html
    assert "disabled" in html and "hidden" not in html
    with pytest.raises(ValueError):
        make_form(transfer_mode="unknown")()
    assert source_configuration()["form_overrides"] is FilteredSelectMultipleField


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["edit/2027", "create"])
async def test_actual_task_templates_load_field_media_once(route):
    ctx = task_console()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ctx.app), base_url="https://fixture.invalid"
    ) as client:
        response = await client.get("/admin/agent-task/" + route)
    assert response.status_code == 200
    field = re.search(r'<select\b(?=[^>]*name="sources")[^>]*>[\s\S]*?</select>', response.text).group()
    assert 'data-filtered-select="plain"' in field and field.count("<option ") == len(ctx.options)
    assert response.text.count('src="/static/js/permission-transfer.js"') == 1
    assert response.text.count('href="/static/css/permission-transfer.css"') == 1
    assert len(re.findall(r"<option selected", field)) == (1 if route.startswith("edit") else 0)
    ctx.view.update_model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("ids,valid", [(["1", "20"], True), ([], True), (["9999"], False)])
async def test_actual_task_post_passes_only_offered_values_to_unchanged_writer(ids, valid):
    ctx = task_console()
    data = {"name": "Task", "save": "Save", "sources": ids}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ctx.app), base_url="https://fixture.invalid"
    ) as client:
        response = await client.post("/admin/agent-task/edit/2027", data=data)
    if valid:
        assert response.status_code == 302
        assert sorted(ctx.view.update_model.call_args.kwargs["data"]["sources"] or []) == sorted(ids)
    else:
        assert response.status_code == 400
        ctx.view.update_model.assert_not_awaited()
        assert 'data-filtered-select="plain"' in response.text


@pytest.mark.asyncio
async def test_widget_does_not_bypass_console_authentication():
    ctx = task_console(allowed=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ctx.app), base_url="https://fixture.invalid"
    ) as client:
        response = await client.get("/admin/agent-task/edit/2027")
    assert response.status_code == 302 and "/login" in response.headers["location"]
    ctx.view.get_object_for_edit.assert_not_awaited()
