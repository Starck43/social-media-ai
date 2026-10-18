"""Isolated real SQLAdmin routes with synthetic ORM and explicit storage doubles."""

from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from source_import_isolation import load_isolated_source
from sqladmin import ModelView
from sqlalchemy import Column, ForeignKey, Integer, String, Table
from sqlalchemy.orm import DeclarativeBase, relationship
from starlette.applications import Starlette
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route
from wtforms import Form, StringField, validators

ROOT = Path(__file__).resolve().parents[1]
RELATED = load_isolated_source("_admin_related_isolated", ROOT / "app/admin/related.py")


class Base(DeclarativeBase):
    pass


links = Table(
    "related_links",
    Base.metadata,
    Column("parent_id", ForeignKey("related_parents.id"), primary_key=True),
    Column("target_id", ForeignKey("related_targets.id"), primary_key=True),
)


class Target(Base):
    __tablename__ = "related_targets"
    id = Column(Integer, primary_key=True)
    name = Column(String)

    def __str__(self):
        return self.name


class Parent(Base):
    __tablename__ = "related_parents"
    id = Column(Integer, primary_key=True)
    target_id = Column(ForeignKey(Target.id))
    target = relationship(Target)
    targets = relationship(Target, secondary=links)


class TargetView(ModelView, model=Target):
    form_columns = ["name"]


class ParentView(ModelView, model=Parent):
    pass


class InputForm(Form):
    name = StringField(validators=[validators.DataRequired()])


@pytest.fixture
async def harness():
    async def static(request):
        return HTMLResponse("")

    app = Starlette(routes=[Route("/static/{path:path}", static, name="static")])
    admin = RELATED.RelatedAdmin(app, templates_dir=str(ROOT / "app/templates"))
    admin.add_view(TargetView)
    admin.add_view(ParentView)
    target, parent = admin._views
    target.scaffold_form = AsyncMock(return_value=InputForm)
    obj = Target(id=7, name="Safe </script><img src=x onerror=alert(1)>")
    target.insert_model = AsyncMock(return_value=obj)
    target.update_model = AsyncMock(return_value=obj)
    target.get_object_for_edit = AsyncMock(return_value=obj)
    target.get_object_for_details = AsyncMock(return_value=obj)
    # Only success/validation use the app's minimal form template; normal
    # console shell imports unrelated app globals, intentionally not booted.
    from jinja2 import ChoiceLoader, DictLoader

    admin.templates.env.loader = ChoiceLoader(
        [
            DictLoader({"isolated-form.html": "<form>{{ form.name() }}{{ error }}</form>"}),
            admin.templates.env.loader,
        ]
    )
    target.create_template = target.edit_template = "isolated-form.html"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://offline.test") as client:
        yield NS(admin=admin, target=target, parent=parent, client=client, obj=obj)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["create", "edit"])
async def test_successful_popup_only_after_normal_crud(harness, mode):
    path = f"/admin/target/{mode}" + ("/7" if mode == "edit" else "")
    result = await harness.client.post(path, data={"name": "Saved", "save": "Save", "_related_token": "a" * 32})
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert '"id": "7"' in result.text
    assert '"identity": "target"' in result.text
    assert "\\u003c/script\\u003e" in result.text
    assert "<img" not in result.text
    writer = harness.target.insert_model if mode == "create" else harness.target.update_model
    writer.assert_awaited_once()
    assert "_related_token" not in writer.call_args.kwargs.get("data", writer.call_args.args[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "", "bad", "a" * 33, "<script>"])
async def test_ordinary_forms_keep_redirect(harness, token):
    data = {"name": "Saved", "save": "Save"}
    if token is not None:
        data["_related_token"] = token
    result = await harness.client.post("/admin/target/create", data=data)
    assert result.status_code == 302
    assert result.headers["location"].endswith("/admin/target/list")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["create", "edit"])
async def test_validation_failure_is_not_a_receipt(harness, mode):
    path = f"/admin/target/{mode}" + ("/7" if mode == "edit" else "")
    result = await harness.client.post(path, data={"name": "", "_related_token": "a" * 32})
    assert result.status_code == 400
    assert "admin-related-saved" not in result.text
    harness.target.insert_model.assert_not_awaited()
    harness.target.update_model.assert_not_awaited()


@pytest.mark.asyncio
async def test_storage_failure_is_not_a_receipt(harness):
    harness.target.insert_model.side_effect = ValueError("synthetic refusal")
    result = await harness.client.post("/admin/target/create", data={"name": "Saved", "_related_token": "a" * 32})
    assert result.status_code == 400
    assert "admin-related-saved" not in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["create", "edit"])
async def test_permission_denial_still_uses_normal_route(harness, mode):
    harness.target.has_permission = lambda *args: False
    path = f"/admin/target/{mode}" + ("/7" if mode == "edit" else "")
    result = await harness.client.post(path, data={"name": "Saved", "_related_token": "a" * 32})
    assert result.status_code == 403
    harness.target.insert_model.assert_not_awaited()
    harness.target.update_model.assert_not_awaited()


def request_for(admin):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "query_string": b"",
            "path": "/admin/parent/create",
            "scheme": "http",
            "server": ("offline.test", 80),
            "headers": [],
            "router": admin.app.router,
            "app": admin.app,
        }
    )


@pytest.mark.asyncio
async def test_only_rendered_registered_fk_m2m_have_controls(harness):
    fields = [
        NS(name="target", id="target", label=NS(text="Target")),
        NS(name="targets", id="targets", label=NS(text="Targets")),
    ]
    controls = await RELATED.related_controls(harness.parent, fields, request_for(harness.admin))
    assert [item["field"] for item in controls] == ["target", "targets"]
    assert controls[0]["edit"].endswith("/target/edit/__related_pk__")
    assert len(await RELATED.related_controls(harness.parent, fields[:1], request_for(harness.admin))) == 1
    harness.target.can_create = harness.target.can_edit = False
    assert await RELATED.related_controls(harness.parent, fields, request_for(harness.admin)) == []


@pytest.mark.asyncio
async def test_target_permission_and_create_checker_are_respected(harness):
    fields = [NS(name="target", id="target", label=NS(text="Target"))]
    harness.target.check_can_create = AsyncMock(return_value=False)
    harness.target.has_permission = lambda *args: False
    assert await RELATED.related_controls(harness.parent, fields, request_for(harness.admin)) == []


@pytest.mark.asyncio
async def test_custom_or_non_post_responses_are_preserved(harness):
    request = request_for(harness.admin)
    request.state.related_saved = {"id": "7"}
    response = HTMLResponse("custom response")
    assert await harness.admin._related_response(request, response) is response
    response = RedirectResponse("/admin/target/list")
    assert await harness.admin._related_response(request, response) is response


@pytest.mark.asyncio
async def test_token_survives_post_validation_url_without_query():
    request = Request({"type": "http", "method": "POST", "path": "/admin/target/create", "headers": []})
    request._form = FormData({"_related_token": "a" * 32})
    assert await RELATED.related_token(request) == "a" * 32
    request._form = FormData({"_related_token": "invalid"})
    assert await RELATED.related_token(request) is None


@pytest.mark.asyncio
async def test_actual_metadata_fragment_is_async_and_json_safe(harness):
    harness.admin.templates.env.globals.update(
        related_controls=RELATED.related_controls, related_token=RELATED.related_token
    )
    form = [NS(name="target", id="target", label=NS(text="</script><img src=x>"))]
    request = request_for(harness.admin)
    html = await harness.admin.templates.env.get_template("sqladmin/related_controls.html").render_async(
        request=request,
        model_view=harness.parent,
        form=form,
        url_for=lambda *args, **kwargs: "/static/admin-related.js",
    )
    assert "\\u003c/script\\u003e" in html
    assert "<img" not in html
    assert html.count('id="admin-related-controls"') == 1
    assert (ROOT / "app/templates/sqladmin/layout.html").read_text().count(
        '{% include "sqladmin/related_controls.html" %}'
    ) == 1


@pytest.mark.asyncio
async def test_missing_target_is_not_a_new_edit_surface(harness):
    fields = [NS(name="target", id="target", label=NS(text="Target"))]
    harness.admin._views = [harness.parent]
    assert await RELATED.related_controls(harness.parent, fields, request_for(harness.admin)) == []


@pytest.mark.asyncio
async def test_object_specific_edit_refusal_is_not_bypassed(harness):
    harness.target.check_can_edit = AsyncMock(return_value=False)
    result = await harness.client.post("/admin/target/edit/7", data={"name": "Saved", "_related_token": "a" * 32})
    assert result.status_code == 403
    harness.target.update_model.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_response_and_missing_receipt_on_post_are_preserved(harness):
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    response = RedirectResponse("/admin/target/list")
    assert await harness.admin._related_response(request, response) is response
    request.state.related_saved = {"id": "7"}
    response = HTMLResponse("custom after-change response")
    assert await harness.admin._related_response(request, response) is response
