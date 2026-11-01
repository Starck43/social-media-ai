"""P8-T46–T49: token-free mocked contracts; no database or live provider access."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.collection import search_categories as registry
from app.services.collection.pages import CollectionProviderError, Page
from app.services.social.credentials import AuthorizationRequired
from app.services.social.rate_limit import SourceRateLimited
from tests.shared.vk_search_contracts import METHODS, ROWS, no_external_search_io, run_search, search_adapter

__source_only__ = True
pytestmark = pytest.mark.usefixtures("no_external_search_io")

CATEGORIES = tuple(ROWS)


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.asyncio
async def test_typed_success_and_exact_official_parameters(category):
    adapter, transport = search_adapter({"count": 8, "items": [ROWS[category]]})
    page = await run_search(adapter, category, limit=3, cursor="2")
    item = page.items[0]
    expected = {
        "video": ("video", "-7_11", "https://vk.com/video-7_11", {"views": 12, "duration": 30}),
        "apps": ("app", "11", "https://vk.com/app11", {"members_count": 12, "app_type": "game"}),
        "market": (
            "market_item",
            "-7_11",
            "https://vk.com/market-7?w=product-7_11",
            {"price": 123.45, "currency": "RUB", "availability": 0},
        ),
        "music": ("audio", "-7_11", "https://vk.com/audio-7_11", {"duration": 30}),
    }[category]
    assert (item["kind"], item["external_id"], item["url"], item["metrics"]) == expected
    assert item["platform"] == "vk"
    assert item["name"] == ("Artist — Demo" if category == "music" else "Demo")
    assert "source_id" not in item and "source_type" not in item
    assert (
        item["evidence"]
        == {
            "video": {"owner_id": -7, "video_id": 11},
            "apps": {"app_id": 11},
            "market": {"owner_id": -7, "market_item_id": 11},
            "music": {"owner_id": -7, "audio_id": 11},
        }[category]
    )
    params = {"q": "design", "count": 3, "offset": 2}
    if category != "music":
        params["extended"] = 1
    if category == "apps":
        params["platform"] = "web"
    if category == "market":
        params["owner_id"] = -7
    transport.assert_awaited_once_with(METHODS[category], params, 42, require_user=False)
    assert page.total_available == 8 and page.next_cursor is None


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("response", [None, {}, {"items": None}, {"count": 0, "items": []}])
@pytest.mark.asyncio
async def test_empty_responses_are_not_permission_failures(category, response):
    adapter, transport = search_adapter(response)
    page = await run_search(adapter, category)
    assert page.items == () and page.next_cursor is None
    assert page.unsupported_reason is None
    transport.assert_awaited_once()


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.asyncio
async def test_partial_page_retains_valid_results_and_reports_dropped_rows(category):
    adapter, _ = search_adapter({"count": 50, "items": [ROWS[category], None, "bad", {}]})
    page = await run_search(adapter, category)
    assert len(page.items) == 1
    assert page.items[0]["external_id"]
    assert "vk_search_partial_response" in page.warnings
    assert page.next_cursor is None
    assert page.total_available == 50


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("items", [42, "not-a-list", {"unexpected": "mapping"}])
@pytest.mark.asyncio
async def test_malformed_items_do_not_crash_or_look_like_clean_empty(category, items):
    adapter, _ = search_adapter({"items": items})
    page = await run_search(adapter, category)
    assert page.items == () and page.next_cursor is None
    assert "vk_search_partial_response" in page.warnings


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("bad_id", [None, True, 0, "broken"])
@pytest.mark.asyncio
async def test_invalid_object_identity_is_not_a_candidate(category, bad_id):
    adapter, _ = search_adapter({"items": [{**ROWS[category], "id": bad_id}]})
    page = await run_search(adapter, category)
    assert page.items == ()
    assert "vk_search_partial_response" in page.warnings


@pytest.mark.parametrize("category", ["video", "market", "music"])
@pytest.mark.parametrize("owner_id", [None, True, 0, "broken"])
@pytest.mark.asyncio
async def test_invalid_owner_identity_is_not_a_candidate(category, owner_id):
    adapter, _ = search_adapter({"items": [{**ROWS[category], "owner_id": owner_id}]})
    assert (await run_search(adapter, category)).items == ()


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize(
    "error,code,retryable",
    [
        (AuthorizationRequired("private provider detail", platform="vk"), "credentials", False),
        (RuntimeError("Ошибка API (1051): private provider detail"), "credentials", False),
        (RuntimeError("Ошибка API (7): private provider detail"), "permission", False),
        (RuntimeError("Ошибка API (15): private provider detail"), "permission", False),
        (RuntimeError("vk_selected_permission_error"), "permission", False),
        (RuntimeError("vk_selected_authorization_error"), "credentials", False),
        (SourceRateLimited("vk", retry_after=60), "rate_limited", True),
        (RuntimeError("private provider detail"), "provider_error", False),
    ],
)
@pytest.mark.asyncio
async def test_errors_are_bounded_and_not_empty_pages(category, error, code, retryable):
    adapter, transport = search_adapter(error=error)
    with pytest.raises(CollectionProviderError) as caught:
        await run_search(adapter, category)
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert "private provider detail" not in str(caught.value)
    transport.assert_awaited_once()  # No category-level retry or credential fallback/fan-out.


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.asyncio
async def test_page_size_and_next_cursor_are_bounded(category):
    adapter, transport = search_adapter({"count": 5000, "items": [ROWS[category]] * 100})
    page = await run_search(adapter, category, limit=5000)
    assert transport.await_args.args[1]["count"] == 100
    assert len(page.items) == 100 and page.next_cursor == "100"
    assert page.limit_reached is False
    transport.assert_awaited_once()


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.asyncio
async def test_last_page_request_is_clamped_before_the_ceiling(category):
    adapter, transport = search_adapter({"count": 5000, "items": [ROWS[category]] * 5})
    page = await run_search(adapter, category, limit=5000, cursor="995")
    assert transport.await_args.args[1]["count"] == 5
    assert page.next_cursor is None and "vk_search_offset_ceiling" in page.warnings


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("cursor", ["1000", "1200"])
@pytest.mark.asyncio
async def test_cursor_at_or_beyond_ceiling_never_calls_provider(category, cursor):
    adapter, transport = search_adapter({"items": [ROWS[category]]})
    page = await run_search(adapter, category, cursor=cursor)
    assert page.items == () and page.next_cursor is None
    assert "vk_search_offset_ceiling" in page.warnings
    transport.assert_not_awaited()


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("limit,cursor", [(0, None), (-1, None), (10, "-1"), (10, "bad")])
@pytest.mark.asyncio
async def test_invalid_bounds_fail_before_provider(category, limit, cursor):
    adapter, transport = search_adapter()
    with pytest.raises(ValueError):
        await run_search(adapter, category, limit=limit, cursor=cursor)
    transport.assert_not_awaited()


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.asyncio
async def test_provider_overdelivery_cannot_exceed_requested_limit(category):
    adapter, _ = search_adapter({"items": [ROWS[category]] * 10})
    page = await run_search(adapter, category, limit=2)
    assert len(page.items) == 2 and page.limit_reached
    assert "vk_search_partial_response" in page.warnings


@pytest.mark.parametrize("owner_id", [None, True, 0, "-7", "club7"])
@pytest.mark.asyncio
async def test_market_scope_fails_closed_before_provider(owner_id):
    adapter, transport = search_adapter()
    with pytest.raises(CollectionProviderError) as caught:
        await run_search(adapter, "market", owner_id=owner_id)
    assert caught.value.code == "vk_owner_invalid"
    transport.assert_not_awaited()


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.asyncio
async def test_shipped_and_forged_category_values_cannot_open_q5_gate(category):
    adapter, transport = search_adapter()
    entry = registry.get_category(category)
    for selected in (category, replace(entry, status="enabled", adapter_operation="search_audios")):
        with pytest.raises(registry.SearchCategoryError) as caught:
            await registry.search_category(adapter, selected, "design", limit=10, owner_id=-7)
        assert caught.value.code == "search_category_unavailable"
    transport.assert_not_awaited()


@pytest.mark.parametrize("category", ["video", "apps", "market"])
@pytest.mark.asyncio
async def test_dispatch_with_test_only_evidence_stamps_provenance(monkeypatch, category):
    entry = registry.get_category(category)
    monkeypatch.setattr(registry, "VK_SEARCH_CATEGORIES", (replace(entry, status="enabled"),))
    adapter, transport = search_adapter({"count": 1, "items": [ROWS[category]]})
    page = await registry.search_category(adapter, category, "design", limit=10, owner_id=-7)
    evidence = page.items[0]["evidence"]
    assert evidence["search_category"] == category and evidence["official_method"] == METHODS[category]
    assert page.total_available == 1
    transport.assert_awaited_once()


@pytest.mark.asyncio
async def test_market_dispatch_requires_owner_even_with_test_only_evidence(monkeypatch):
    entry = registry.get_category("market")
    monkeypatch.setattr(registry, "VK_SEARCH_CATEGORIES", (replace(entry, status="enabled"),))
    adapter, transport = search_adapter()
    with pytest.raises(registry.SearchCategoryError) as caught:
        await registry.search_category(adapter, "market", "design", limit=10)
    assert caught.value.code == "search_category_owner_required"
    transport.assert_not_awaited()


@pytest.mark.asyncio
async def test_music_has_an_independent_rights_gate_even_with_test_only_binding(monkeypatch):
    music = registry.get_category("music")
    assert music.status == "deferred" and music.reason == "separate_permission_evidence_required"
    assert music.adapter_operation is None and music.official_method is None
    monkeypatch.setattr(
        registry, "VK_SEARCH_CATEGORIES", (replace(music, status="enabled", adapter_operation="search_audios"),)
    )
    operation = AsyncMock(return_value=Page())
    with pytest.raises(registry.SearchCategoryError) as caught:
        await registry.search_category(SimpleNamespace(search_audios=operation), "music", "design", limit=10)
    assert caught.value.code == "search_category_unavailable"
    operation.assert_not_awaited()


def test_catalog_keeps_official_apps_method_and_all_gates_closed():
    selection = registry.resolve_categories("all")
    assert selection.selected == ()
    catalog = {entry["id"]: entry for entry in registry.category_catalog()}
    for category in ("video", "apps", "market"):
        assert catalog[category]["status"] == "mocked"
        assert catalog[category]["official_method"] == METHODS[category]
        assert catalog[category]["reason"] == "vk_method_profile_unverified"
        assert catalog[category]["evidence_ref"] == "docs/tasks/q-5.md"
    assert catalog["music"]["reason"] == "separate_permission_evidence_required"
    assert not any(entry["executable"] for entry in catalog.values())


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("code,attempts,pauses", [(6, 3, [1.0, 2.0]), (9, 1, []), (29, 1, [])])
@pytest.mark.asyncio
async def test_real_client_quota_loop_is_bounded_without_tokens(monkeypatch, category, code, attempts, pauses):
    from app.services.social import base, rate_limit
    from tests.shared.vk_search_contracts import quota_adapter

    monkeypatch.setattr(rate_limit.settings, "SOCIAL_RATE_LIMIT_RETRIES", 2)
    monkeypatch.setattr(rate_limit.settings, "SOCIAL_RATE_LIMIT_MAX_WAIT", 5)
    sleep = AsyncMock()
    monkeypatch.setattr(base.asyncio, "sleep", sleep)
    adapter, transport = quota_adapter([{"error": {"error_code": code}}] * 3)
    with pytest.raises(CollectionProviderError) as caught:
        await run_search(adapter, category)
    assert caught.value.code == "rate_limited" and caught.value.retryable
    assert transport.await_count == attempts
    assert [call.args[0] for call in sleep.await_args_list] == pauses
    assert all(call.args[0] == METHODS[category] for call in transport.await_args_list)
    assert all("access_token" not in call.args[1] for call in transport.await_args_list)


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.asyncio
async def test_real_client_short_quota_recovers_once_without_fanout(monkeypatch, category):
    from app.services.social import base, rate_limit
    from tests.shared.vk_search_contracts import quota_adapter

    monkeypatch.setattr(rate_limit.settings, "SOCIAL_RATE_LIMIT_RETRIES", 2)
    monkeypatch.setattr(rate_limit.settings, "SOCIAL_RATE_LIMIT_MAX_WAIT", 5)
    sleep = AsyncMock()
    monkeypatch.setattr(base.asyncio, "sleep", sleep)
    adapter, transport = quota_adapter(
        [
            {"error": {"error_code": 6}},
            {"response": {"count": 1, "items": [ROWS[category]]}},
        ]
    )
    page = await run_search(adapter, category)
    assert len(page.items) == 1 and transport.await_count == 2
    assert transport.await_args_list[0] == transport.await_args_list[1]
    sleep.assert_awaited_once_with(1.0)


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.asyncio
async def test_full_partial_page_advances_by_raw_slots_not_valid_items(category):
    adapter, _ = search_adapter({"count": 1000, "items": [ROWS[category]] + [None] * 99})
    page = await run_search(adapter, category, limit=500)
    assert len(page.items) == 1 and page.next_cursor == "100"
    assert "vk_search_partial_response" in page.warnings


@pytest.mark.asyncio
async def test_market_response_cannot_escape_the_requested_owner():
    adapter, _ = search_adapter({"items": [{**ROWS["market"], "owner_id": -99}]})
    page = await run_search(adapter, "market", owner_id=-7)
    assert page.items == () and "vk_search_partial_response" in page.warnings


@pytest.mark.parametrize(
    "screen_name,expected", [("example", "https://vk.com/example"), (None, "https://vk.com/app11")]
)
@pytest.mark.asyncio
async def test_app_public_link_has_a_stable_id_fallback(screen_name, expected):
    adapter, _ = search_adapter({"items": [{**ROWS["apps"], "screen_name": screen_name}]})
    assert (await run_search(adapter, "apps")).items[0]["url"] == expected


@pytest.mark.parametrize("amount", [None, "unknown"])
@pytest.mark.asyncio
async def test_missing_market_price_is_unknown_not_zero(amount):
    adapter, _ = search_adapter({"items": [{**ROWS["market"], "price": {"amount": amount}}]})
    metrics = (await run_search(adapter, "market")).items[0]["metrics"]
    assert metrics["price"] is None and metrics["currency"] is None


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("retry_after,attempts,pauses", [("2", 2, [2.0]), ("60", 1, [])])
@pytest.mark.asyncio
async def test_http_429_honors_bounded_retry_after(monkeypatch, category, retry_after, attempts, pauses):
    import httpx

    from app.services.social import base, rate_limit
    from tests.shared.vk_search_contracts import quota_adapter

    monkeypatch.setattr(rate_limit.settings, "SOCIAL_RATE_LIMIT_RETRIES", 1)
    monkeypatch.setattr(rate_limit.settings, "SOCIAL_RATE_LIMIT_MAX_WAIT", 5)
    sleep = AsyncMock()
    monkeypatch.setattr(base.asyncio, "sleep", sleep)
    response = httpx.Response(
        429, headers={"Retry-After": retry_after}, request=httpx.Request("POST", "https://vk.invalid/offline")
    )
    error = httpx.HTTPStatusError("private provider detail", request=response.request, response=response)
    adapter, transport = quota_adapter([error, error])
    with pytest.raises(CollectionProviderError) as caught:
        await run_search(adapter, category)
    assert caught.value.code == "rate_limited" and caught.value.retryable
    assert "private provider detail" not in str(caught.value)
    assert transport.await_count == attempts
    assert [call.args[0] for call in sleep.await_args_list] == pauses
