"""Isolated source/mocked handlers and real template rendering; no app, DB or provider."""

import ast
import inspect
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import Form, HTTPException
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape
from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]


def model(**changes):
    values = dict(
        id=7,
        name="Example <script>bad()</script>",
        model_id="example/model",
        model_type="text,image",
        max_tokens=1024,
        default_temperature=0.3,
        is_active=True,
        input_cost_per_1k=0.0000001,
        output_cost_per_1k=0.002,
        last_success_at=None,
        last_error_at=None,
        provider=NS(name="Provider", api_format="openai", is_active=True, get_api_key=lambda: "fake-test-key"),
    )
    values.update(changes)
    return NS(**values)


def probe_module(response=None, status=200):
    client = NS(
        api_key="fake-test-key",
        model_name="example/model",
        base_url="https://provider.invalid",
        _headers=lambda: {},
    )
    post = AsyncMock(return_value=NS(status_code=status, json=lambda: response))
    http = NS(AsyncClient=Mock(), TimeoutException=httpx.TimeoutException, TransportError=httpx.TransportError)
    http.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=NS(post=post))
    http.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)
    # SimpleNamespace is not an async context manager on the type, so use AsyncMock.
    http.AsyncClient.return_value = AsyncMock()
    http.AsyncClient.return_value.__aenter__.return_value = NS(post=post)

    async def chat(messages, **kwargs):
        # Explicit client boundary double; never import the app/DB client here.
        await post(
            json={"messages": messages, "max_tokens": kwargs["max_tokens"]},
            timeout=client.timeout,
        )
        if status != 200:
            raise RuntimeError("mock provider failure")
        choices = response.get("choices", []) if isinstance(response, dict) else []
        content = choices[0].get("message", {}).get("content") if choices else None
        return {"content": content, "raw": response, "_personal_dispatch_id": "mock-dispatch"}

    client.chat = AsyncMock(side_effect=chat)
    module = load_isolated_source(
        "_model_probe",
        ROOT / "app/services/ai/model_probe.py",
        {
            "app.services.ai.llm_client": NS(
                LLMClientFactory=NS(create=lambda m: client),
                price_usage_usd=lambda *a: 0.01,
                journal_chat_attempt=AsyncMock(),
            ),
            "app.core.config": NS(settings=NS(PERSONAL_BILLING_ENABLED=True)),
        },
        module_imports={"httpx": http},
    )
    return module, post


@pytest.mark.parametrize("state", [None, model(is_active=False), model(last_error_at="error")])
def test_short_fails_closed_without_provider_traffic(state):
    module, post = probe_module()
    assert module.probe_short(state)["ok"] is False
    post.assert_not_awaited()


def test_short_is_prompt_free_and_never_escalates():
    module, post = probe_module()
    answer = module.probe_short(model())
    assert answer["ok"] and answer["tokens_spent"] == 0
    assert "не вызывался" in answer["text"]
    module.probe_mock = Mock(side_effect=ValueError("PRIVATE_SENTINEL"))
    answer = module.probe_short(model())
    assert not answer["ok"] and "PRIVATE_SENTINEL" not in str(answer)
    post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage,expected",
    [
        ({}, None),
        ({"prompt_tokens": 0, "completion_tokens": 0}, 0),
        ({"prompt_tokens": 2, "completion_tokens": 3}, 5),
        ({"prompt_tokens": True, "completion_tokens": -1}, None),
    ],
)
async def test_real_probe_is_capped_and_missing_usage_is_unknown(usage, expected):
    module, post = probe_module({"choices": [{"message": {"content": "да"}}], "usage": usage})
    answer = await module.probe_real_capped(model())
    assert answer["ok"] and answer["tokens_spent"] == expected
    args = post.await_args.kwargs
    assert args["json"]["max_tokens"] == 8 and args["timeout"] == 20
    assert args["json"]["messages"][0]["content"] == module.PROBE_PROMPT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,status",
    [({}, 500), ({"choices": []}, 200), ({"choices": [{"message": {"content": " "}}]}, 200)],
)
async def test_real_failures_do_not_claim_success(response, status):
    module, _ = probe_module(response, status)
    assert not (await module.probe_real_capped(model()))["ok"]


def action_module(probe=None):
    probe = probe or probe_module()[0]
    templates = NS(TemplateResponse=AsyncMock(side_effect=lambda request, name, context: context))
    manager = NS(select_related=lambda *a: manager, get=AsyncMock(return_value=model()), update_by_id=AsyncMock())
    module = load_isolated_source(
        "_admin_model_actions",
        ROOT / "app/admin/actions.py",
        {
            "app.models": NS(LLMModel=NS(objects=manager), Tenant=NS()),
            "app.core.config": NS(settings=NS(PERSONAL_BILLING_ENABLED=False)),
            "app.services.ai.model_probe": probe,
        },
    )
    return module, manager, NS(templates=templates)


@pytest.mark.asyncio
async def test_get_never_executes_legacy_query_modes():
    probe, post = probe_module()
    actions, _, view = action_module(probe)
    result = await actions.LLMModelActions.test_model(
        view, NS(query_params={"test_type": "real", "prompt": "send"}), "7", "llm-model"
    )
    assert result["result"] is None
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_short_post_needs_no_question_and_calls_api_service():
    probe, post = probe_module()
    probe.probe_connectivity = AsyncMock(return_value={"ok": True, "tier": "connectivity", "tokens_spent": 0})
    actions, _, view = action_module(probe)
    result = await actions.LLMModelActions.submit_test(view, NS(), "7", "short", "")
    assert result["result"]["ok"]
    probe.probe_connectivity.assert_awaited_once()
    post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,prompt", [("other", ""), ("short", "x" * 8001)])
async def test_invalid_mode_or_oversize_is_rejected_before_model_lookup(mode, prompt):
    actions, manager, view = action_module()
    with pytest.raises(HTTPException) as error:
        await actions.LLMModelActions.submit_test(view, NS(), "7", mode, prompt)
    assert error.value.status_code == 422
    manager.get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("pk", ["7,8", "oops", "0", "-1", "2147483648", "9223372036854775807"])
async def test_bad_selection_is_not_a_lookup(pk):
    actions, manager, view = action_module()
    with pytest.raises(HTTPException) as error:
        await actions.LLMModelActions.test_model(view, NS(), pk, "llm-model")
    assert error.value.status_code == 422
    manager.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_maximum_integer_model_id_can_be_missing_without_overflow():
    actions, manager, view = action_module()
    manager.get.return_value = None
    with pytest.raises(HTTPException) as error:
        await actions.LLMModelActions.submit_test(view, NS(), "2147483647", "short", "")
    assert error.value.status_code == 404
    manager.get.assert_awaited_once_with(id=2147483647)


@pytest.mark.asyncio
async def test_missing_model_and_empty_mock_are_truthful():
    actions, manager, view = action_module()
    result = await actions.LLMModelActions.submit_test(view, NS(), "7", "mock", "   ")
    assert not result["result"]["ok"]
    manager.get.return_value = None
    with pytest.raises(HTTPException) as error:
        await actions.LLMModelActions.test_model(view, NS(), "7", "llm-model")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_real_forwards_question_and_preserves_unknown_estimate():
    probe, post = probe_module({"choices": [{"message": {"content": "да"}}]})
    actions, manager, view = action_module(probe)
    result = await actions.LLMModelActions.submit_test(view, NS(), "7", "real", "PRIVATE_PROMPT")
    assert result["result"]["ok"] and result["result"]["estimated_cost"] is None
    assert post.await_args.kwargs["json"]["messages"][0]["content"] == "PRIVATE_PROMPT"
    assert post.await_args.kwargs["json"]["max_tokens"] == 512
    manager.update_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_non_openai_refuses_before_dispatch():
    probe, post = probe_module()
    actions, manager, view = action_module(probe)
    manager.get.return_value.provider.api_format = "anthropic"
    result = await actions.LLMModelActions.submit_test(view, NS(), "7", "real", "")
    assert not result["result"]["ok"]
    post.assert_not_awaited()


def extracted_handler(path, name, globals_, imports):
    tree = ast.parse((ROOT / path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    node.decorator_list = []
    # Keep the real body and resolve annotations lazily; only explicit import doubles.
    code = compile(
        ast.fix_missing_locations(
            ast.Module(
                body=[
                    ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                    node,
                ],
                type_ignores=[],
            )
        ),
        str(ROOT / path),
        "exec",
    )
    import builtins

    normal = builtins.__import__

    def isolated(name, globals=None, locals=None, fromlist=(), level=0):
        if name in imports:
            return imports[name]
        return normal(name, globals, locals, fromlist, level)

    ns = dict(globals_, __builtins__=dict(vars(builtins), __import__=isolated))
    exec(code, ns)
    return ns[name]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allowed,csrf,confirmed,expected",
    [(False, True, True, 403), (True, False, True, 403), (True, True, False, None), (True, True, True, None)],
)
async def test_post_requires_fresh_configure_csrf_but_no_checkbox(allowed, csrf, confirmed, expected):
    async def guard(*args):
        if not allowed:
            raise HTTPException(403, "denied")
        return NS(is_platform_admin=True)

    submit = AsyncMock(return_value="rendered")
    handler = extracted_handler(
        "app/admin/endpoints.py",
        "submit_llm_model_test",
        {
            "require_admin_perm": guard,
            "ActionType": NS(CONFIGURE="configure"),
            "HTMLResponse": object,
            "HTTPException": HTTPException,
            "get_csrf_manager": lambda request: NS(verify_token=lambda token: csrf),
            "settings": NS(PERSONAL_BILLING_ENABLED=True),
            "Tenant": NS(objects=NS(get=AsyncMock(return_value=NS(id=3, is_active=True)))),
        },
        {
            "app.admin.actions": NS(LLMModelActions=NS(submit_test=submit)),
            "app.core.permissions": NS(permission_scope=lambda user: __import__("contextlib").nullcontext()),
            "app.core.tenant_context": NS(tenant_scope=lambda tenant: __import__("contextlib").nullcontext()),
        },
    )
    request = NS(
        form=AsyncMock(
            return_value={
                "csrf_token": "fake-csrf",
                "model_id": "7",
                "test_type": "real",
                "confirm_real": "yes" if confirmed else "",
                "tenant_id": "3",
            }
        ),
        app=NS(state=NS(admin="view")),
        state=NS(),
    )
    if expected:
        with pytest.raises(HTTPException) as error:
            await handler(request)
        assert error.value.status_code == expected
        submit.assert_not_awaited()
    else:
        assert await handler(request) == "rendered"
        submit.assert_awaited_once()


def template_env():
    from importlib.metadata import distribution

    package_templates = distribution("sqladmin").locate_file("sqladmin/templates")
    env = Environment(
        loader=ChoiceLoader([FileSystemLoader(ROOT / "app/templates"), FileSystemLoader(package_templates)]),
        autoescape=select_autoescape(["html", "xml"]),
    )

    def url(name, **kw):
        if name == "static":
            return "/static/" + kw["path"]
        return "/admin/" + kw.get("identity", "") + "/" + name.split(":")[-1]

    env.globals.update(url_for=url, csrf_token=lambda: "fake-csrf")
    return env


@pytest.mark.parametrize(
    "result",
    [
        None,
        {"ok": True, "tier": "mock", "text": "OK"},
        {"ok": False, "tier": "real", "text": "Error"},
        {"ok": True, "tier": "real", "text": "OK", "content": "<script>bad()</script>", "tokens_spent": None},
    ],
)
def test_actual_template_renders_partial_states_and_escapes(result):
    # Render the real child blocks without a running console/menu/DB.
    env = template_env()
    env.loader = ChoiceLoader([FileSystemLoader(ROOT / "app/templates"), env.loader])
    template = env.from_string(
        (ROOT / "app/templates/llm_model/test_results.html")
        .read_text()
        .replace('{% extends "sqladmin/layout.html" %}', "")
    )
    rendered = template.render(
        model=model(),
        provider=model().provider,
        prompt='"</textarea><script>bad()</script>',
        result=result,
        status_checks=[("Active", True)],
        status_hint="Local",
        probe_prompt="да",
    )
    assert "<script>bad()" not in rendered
    assert 'name="test_type" value="short"' in rendered and 'method="post"' in rendered
    assert 'name="csrf_token"' in rendered and 'name="confirm_real"' not in rendered
    assert 'type="file"' not in rendered


def test_model_view_display_and_telemetry_are_not_editable():
    source = ast.parse((ROOT / "app/admin/views.py").read_text())
    view = next(c for c in source.body if isinstance(c, ast.ClassDef) and c.name == "LLMModelAdmin")
    form = next(
        ast.literal_eval(n.value)
        for n in view.body
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "form_columns" for t in n.targets)
    )
    assert "provider" in form and "last_request_cost" not in form and "use_count" not in form
    assert "column_formatters_detail = column_formatters" in ast.unparse(view)
    # Isolate the pure formatter without importing app/SQLAlchemy.
    tree = ast.parse((ROOT / "app/admin/formatters.py").read_text())
    f = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "model_price")
    ns = {}
    exec(compile(ast.Module(body=[f], type_ignores=[]), "formatter", "exec"), ns)
    assert ns["model_price"](0.0000001) == "$0.0000001"
    assert ns["model_price"](0) == "$0"
    assert ns["model_price"](None) == "—"


@pytest.mark.asyncio
async def test_real_sqladmin_llm_view_scaffolds_against_canonical_metadata_without_db():
    """Exercise SQLAdmin's actual view/form API with canonical ORM metadata only."""
    from sqladmin import ModelView, action
    from sqladmin.fields import SelectField
    from sqlalchemy import Column, ForeignKey, Integer
    from sqlalchemy.orm import relationship
    from wtforms import FloatField, widgets

    settings = NS(DB_SCHEMA="fixture_schema")
    config = NS(settings=settings)
    decorators = NS(app_label=lambda label: lambda cls: cls)
    base = load_isolated_source(
        "app.models._llm_fixture_base", ROOT / "app/models/base.py", {"app.core.config": config}
    )
    dependencies = {"app.core.config": config, "app.core.decorators": decorators, "app.models.base": base}
    provider_module = load_isolated_source(
        "app.models.llm._fixture_provider", ROOT / "app/models/llm/llm_provider.py", dependencies
    )
    model_module = load_isolated_source(
        "app.models.llm._fixture_model", ROOT / "app/models/llm/llm_model.py", dependencies
    )
    provider_class, model_class = provider_module.LLMProvider, model_module.LLMModel

    class AgentScenario(base.Base):
        __tablename__ = "scenario_fixture"
        id = Column(Integer, primary_key=True)
        text_llm_model_id = Column(Integer, ForeignKey("fixture_schema.llm_models.id"))
        image_llm_model_id = Column(Integer, ForeignKey("fixture_schema.llm_models.id"))
        video_llm_model_id = Column(Integer, ForeignKey("fixture_schema.llm_models.id"))
        text_llm_model = relationship("LLMModel", foreign_keys=[text_llm_model_id], back_populates="text_scenarios")
        image_llm_model = relationship("LLMModel", foreign_keys=[image_llm_model_id], back_populates="image_scenarios")
        video_llm_model = relationship("LLMModel", foreign_keys=[video_llm_model_id], back_populates="video_scenarios")

    providers = NS(
        all=lambda: providers,
        order_by=AsyncMock(return_value=[provider_class(id=1, name="Provider", base_url="https://provider.invalid")]),
    )
    provider_class.objects = providers
    widget = load_isolated_source(
        "_llm_widgets",
        ROOT / "app/admin/widgets.py",
        {"app.utils.date_parsing": NS(universal_date_parser=Mock())},
    )
    from decimal import Decimal

    class BaseAdmin(ModelView):
        form_excluded_columns = ["created_at", "updated_at"]
        column_labels = {}
        column_formatters = {}
        form_args = {}
        form_overrides = {"is_active": SelectField, "is_default": SelectField}

    node = next(
        n
        for n in ast.parse((ROOT / "app/admin/views.py").read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == "LLMModelAdmin"
    )
    ns = dict(
        BaseAdmin=BaseAdmin,
        LLMModel=model_class,
        LLMProvider=provider_class,
        ActionType=NS(CONFIGURE="configure"),
        CommaSeparatedSelectMultipleField=widget.CommaSeparatedSelectMultipleField,
        widgets=widgets,
        FloatField=FloatField,
        model_price=lambda value: "$" + format(Decimal(str(value)), "f"),
        action=action,
    )
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[
                        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                        node,
                    ],
                    type_ignores=[],
                )
            ),
            "LLMModelAdmin",
            "exec",
        ),
        ns,
    )
    view = ns["LLMModelAdmin"]()
    session = Mock()
    session.execute.return_value.scalars.return_value.unique.return_value.all.return_value = []
    factory = Mock()
    factory.class_ = object
    factory.return_value.__enter__ = Mock(return_value=session)
    factory.return_value.__exit__ = Mock(return_value=False)
    view.session_maker = factory
    form = await view.scaffold_form()
    assert hasattr(form, "provider") and hasattr(form, "model_type")
    assert not hasattr(form, "use_count") and not hasattr(form, "last_request_cost")
    assert set(view._list_prop_names) == set(view.column_list)
    assert set(view.column_details_list) <= set(model_class.__mapper__.attrs.keys())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allowed,csrf,exists", [(True, True, True), (False, True, True), (True, False, True), (True, True, False)]
)
async def test_web_handler_preserves_guards_and_calls_same_short_service(allowed, csrf, exists):
    from starlette.responses import RedirectResponse

    probe, post = probe_module()
    probe.probe_short = Mock(wraps=probe.probe_short)
    selected = model()
    manager = NS(select_related=lambda *a: manager, filter=lambda **kw: manager, first=AsyncMock(return_value=selected))
    tenant_manager = NS(get=AsyncMock(return_value=NS(id=3) if exists else None))
    handler = extracted_handler(
        "app/web/settings.py",
        "agent_model_probe",
        {
            "Form": Form,
            "action_tenant_id": lambda request, tenant: tenant,
            "_guard_workspace_settings": lambda *a: None if allowed else "denied",
            "ensure_csrf": lambda *a: csrf,
            "add_flash": Mock(),
            "BACK": "/app/settings",
            "LLMModel": NS(objects=manager),
            "RedirectResponse": RedirectResponse,
        },
        {
            "app.models.managers.tenant_manager": NS(tenants=tenant_manager),
            "app.services.ai.model_probe": probe,
        },
    )
    request = NS(session={})
    await handler(request, agent_model="7", token="fake-csrf", tenant_id=3)
    if allowed and csrf and exists:
        probe.probe_short.assert_called_once_with(selected)
        assert request.session["agent_model_probe_result"]["ok"]
    else:
        probe.probe_short.assert_not_called()
        assert not request.session
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_known_real_estimate_and_failed_save_do_not_retry_paid_request():
    probe, post = probe_module(
        {"choices": [{"message": {"content": "да"}}], "usage": {"prompt_tokens": 2, "completion_tokens": 3}}
    )
    actions, manager, view = action_module(probe)
    manager.update_by_id.side_effect = RuntimeError("PRIVATE_SENTINEL")
    result = (await actions.LLMModelActions.submit_test(view, NS(), "7", "real", ""))["result"]
    assert result["ok"] and result["estimated_cost"] is not None
    assert "автоматически" in result["save_warning"] and "PRIVATE_SENTINEL" not in str(result)
    post.assert_awaited_once()
    manager.update_by_id.assert_awaited_once()
