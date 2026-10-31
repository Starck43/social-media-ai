"""Isolated form contract tests; catalog persistence is explicitly doubled."""

import runpy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import FormData

ROOT = Path(__file__).resolve().parents[2]
load = runpy.run_path(str(ROOT / "tests/source_import_isolation.py"))["load_isolated_source"]
fixtures = runpy.run_path(str(ROOT / "tests/shared/settings_llm_fixtures.py"))


def source():
    service = NS(
        catalog_metadata=AsyncMock(return_value={"providers": [], "models": [], "effective_default_model_id": None}),
        save_provider_bundle=AsyncMock(),
        set_default_model=AsyncMock(),
    )
    flashes = []
    deps = NS(
        add_flash=lambda request, level, message: flashes.append((level, message)),
        ensure_csrf=lambda request, token: token == "fixture-csrf",
    )
    from unittest.mock import Mock

    manager = NS(filter=AsyncMock(return_value=[]))
    model_manager = NS(LLMModelManager=Mock(return_value=manager))
    module = load(
        "app.web._isolated_llm_settings",
        ROOT / "app/web/settings_llm.py",
        {
            "app.web.deps": deps,
            "app.services.ai.personal_catalog": service,
            "app.models.managers.llm_model_manager": model_manager,
        },
    )
    return module, service, flashes


def form(**extra):
    return FormData(
        {
            "provider_name": "Qwen.AI",
            "provider_uid": "qwen",
            "base_url": "https://fixture.invalid/v1",
            "_csrf": "fixture-csrf",
            **extra,
        }
    )


def request(data=None, user_id=2, active=True):
    return NS(
        state=NS(web_user=NS(id=user_id, is_active=active), tenant=None),
        session={},
        form=AsyncMock(return_value=data or form()),
    )


def test_bundle_sparse_update_preserves_unexposed_settings():
    module, _, _ = source()
    provider, models = module._bundle(form(provider_id="21", owner_user_id="999", api_key=""))
    assert provider == {"id": 21, "name": "Qwen.AI", "uid": "qwen", "base_url": "https://fixture.invalid/v1"}
    assert models == []
    assert "api_key" not in provider


def test_multiple_models_and_editable_provider_uid():
    module, _, _ = source()
    data = FormData(
        [
            *form(provider_id="21", provider_uid="renamed").multi_items(),
            ("model_db_id", "31"),
            ("model_name", "Updated"),
            ("model_api_id", "api-new"),
            ("model_db_id", ""),
            ("model_name", "New"),
            ("model_api_id", "api-added"),
        ]
    )
    provider, models = module._bundle(data)
    assert provider["uid"] == "renamed"
    assert models == [{"id": 31, "name": "Updated", "model_id": "api-new"}, {"name": "New", "model_id": "api-added"}]


@pytest.mark.parametrize("identifier", ["-1", "0", "nan", "1.2", " 2", "true", "１２"])
def test_ids_strict(identifier):
    module, _, _ = source()
    with pytest.raises(ValueError):
        module._bundle(form(provider_id=identifier))


@pytest.mark.parametrize(
    "items",
    [
        [("model_name", "Only name")],
        [("model_name", "Name"), ("model_api_id", ""), ("model_db_id", "")],
        [
            ("model_name", "A"),
            ("model_api_id", "a"),
            ("model_db_id", "31"),
            ("model_name", "B"),
            ("model_api_id", "b"),
            ("model_db_id", "31"),
        ],
    ],
)
def test_invalid_rows_rejected(items):
    module, _, _ = source()
    with pytest.raises(ValueError):
        module._bundle(FormData([*form().multi_items(), *items]))


@pytest.mark.asyncio
async def test_save_trusted_actor_only():
    module, service, flashes = source()
    response = await module.save_bundle(request(form(owner_user_id="999", provider_id="21")))
    assert response.status_code == 303
    assert response.headers["location"] == module.BACK
    assert service.save_provider_bundle.await_args.kwargs["user_id"] == 2
    assert "owner_user_id" not in service.save_provider_bundle.await_args.kwargs["provider"]
    assert flashes[0][0] == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", ["save_bundle", "set_default"])
async def test_csrf_no_service_calls(handler):
    module, service, _ = source()
    response = await getattr(module, handler)(request(form(_csrf="bad")))
    assert response.status_code == 403
    service.save_provider_bundle.assert_not_awaited()
    service.set_default_model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id,active", [(2, False), (None, True), (True, True), (-1, True)])
async def test_inactive_or_unknown_actor_fails_closed(user_id, active):
    module, service, _ = source()
    response = await module.save_bundle(request(user_id=user_id, active=active))
    assert response.status_code == 403
    service.save_provider_bundle.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_calls_shared_service():
    module, service, _ = source()
    await module.set_default(request(form(model_id="31")))
    service.set_default_model.assert_awaited_once_with(user_id=2, model_id=31)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [PermissionError("secret-fixture-detail"), ValueError("secret-fixture-detail")])
async def test_errors_do_not_echo_payload(error):
    module, service, flashes = source()
    service.save_provider_bundle.side_effect = error
    response = await module.save_bundle(request(form(api_key="fixture-key-not-real")))
    assert response.status_code == 303
    assert "secret-fixture-detail" not in str(flashes)
    assert "fixture-key-not-real" not in str(flashes)


@pytest.mark.asyncio
async def test_context_without_workspace():
    module, service, _ = source()
    context = await module.settings_llm_context(request())
    service.catalog_metadata.assert_awaited_once_with(user_id=2)
    assert context["llm_chat_model_id"] is None


@pytest.mark.parametrize("mode", ["catalog", "empty", "no-workspace"])
def test_strict_template_and_no_credential_projection(mode):
    html = fixtures["render"](mode)
    assert "Модели ИИ" in html
    assert "encrypted_api_key" not in html
    assert 'name="owner_user_id"' not in html
    if mode != "empty":
        assert "По умолчанию" in html
        assert 'name="provider_uid"' in html
    if mode == "catalog":
        assert "Выбрана для чата" in html


def test_row_limit_is_enforced_before_service():
    module, _, _ = source()
    rows = [
        (key, value)
        for _ in range(51)
        for key, value in (("model_db_id", ""), ("model_name", "Name"), ("model_api_id", "api"))
    ]
    with pytest.raises(ValueError):
        module._bundle(FormData([*form().multi_items(), *rows]))


def test_blank_new_row_is_ignored_and_existing_row_cannot_be_silently_dropped():
    module, _, _ = source()
    _, models = module._bundle(
        FormData([*form().multi_items(), ("model_db_id", ""), ("model_name", ""), ("model_api_id", "")])
    )
    assert models == []
    with pytest.raises(ValueError):
        module._bundle(
            FormData([*form().multi_items(), ("model_db_id", "31"), ("model_name", ""), ("model_api_id", "")])
        )


def test_catalog_markup_escapes_provider_data():
    context = fixtures["catalog_context"]()
    context["llm_catalog"]["providers"][0]["name"] = '</script><script>alert("fixture")</script>'
    context["llm_provider_choices"][0]["name"] = context["llm_catalog"]["providers"][0]["name"]
    environment = fixtures["BASE"]["environment"]()
    html = environment.get_template("web/settings.html").render(**context)
    assert '<script>alert("fixture")</script>' not in html
    assert "\\u003c/script\\u003e" in html


@pytest.mark.asyncio
async def test_prefill_projection_does_not_serialize_decimal_tariffs_or_model_data():
    module, service, _ = source()
    context = fixtures["catalog_context"]()
    context["llm_catalog"]["providers"][0]["models"][0]["cache_read_cost_per_1m"] = Decimal("0.01")
    service.catalog_metadata.return_value = context["llm_catalog"]
    result = await module.settings_llm_context(request())
    assert set(result["llm_provider_choices"][0]) == {"id", "name", "uid", "base_url", "has_api_key"}
    context.update(result)
    html = fixtures["BASE"]["environment"]().get_template("web/settings.html").render(**context)
    assert "cache_read_cost_per_1m" not in html
