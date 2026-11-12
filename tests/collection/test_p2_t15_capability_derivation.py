"""P2-T15 connector-owned capability derivation and workspace scoping."""

from types import SimpleNamespace as NS

import pytest

from app.core.tenant_context import TenantContextError, tenant_scope
from app.services.collection.entitlement_policy import required_capabilities
from app.services.collection.entitlements import intent_capabilities, source_capabilities

__source_only__ = True
pytestmark = pytest.mark.tenancy

VK_READ = "connector.vk.read"
VK_SEARCH = "connector.vk.search"
TG_READ = "connector.telegram.read"


def _source(source_id, tenant_id, platform, source_type="channel"):
    return NS(
        id=source_id,
        tenant_id=tenant_id,
        platform=NS(platform_type=NS(db_value=platform)),
        source_type=NS(db_value=source_type),
        is_active=True,
    )


def test_source_capabilities_cover_read_search_and_unknown_adapters():
    sources = [
        _source(1, 7, "vk"),
        _source(2, 7, "vk", "search"),
        _source(3, 7, "telegram"),
        _source(4, 7, "future"),
    ]

    assert source_capabilities(sources) == {
        VK_READ,
        VK_SEARCH,
        TG_READ,
        "connector.future.read",
    }


@pytest.mark.parametrize(
    ("payload", "fallback", "expected"),
    [
        ({"collection": {"queries": ["repair"]}}, "vk", {VK_SEARCH}),
        ({"task_plan": {"platforms": ["telegram"]}, "collection": {"queries": ["repair"]}}, "vk", set()),
        (
            {
                "task_plan": {"platforms": ["vk"]},
                "collection": {"discovery": {"queries": ["design"], "people": True, "communities": False}},
            },
            "telegram",
            {VK_READ, VK_SEARCH},
        ),
        ({"collection": {}}, "vk", set()),
    ],
)
def test_intent_capabilities_are_derived_only_from_collection_operations(payload, fallback, expected):
    assert intent_capabilities(payload, fallback_platform=fallback) == expected


async def test_effective_sources_add_connector_reads_and_respect_platform_scope():
    sources = [_source(1, 7, "vk"), _source(2, 7, "telegram"), _source(3, 7, "vk", "search")]
    payload = {
        "task_plan": {"platforms": ["vk"], "source_scope": "all"},
        "collection": {"queries": ["repair"]},
    }

    with tenant_scope(7):
        required = await required_capabilities(None, payload, sources=sources)

    assert required == {VK_READ, VK_SEARCH}


async def test_legacy_sources_choose_vk_then_first_linked_platform_as_intent_fallback():
    telegram = _source(1, 7, "telegram")
    vk = _source(2, 7, "vk")
    payload = {"collection": {"queries": ["repair"]}}

    with tenant_scope(7):
        telegram_only = await required_capabilities(None, payload, sources=[telegram])
        mixed = await required_capabilities(None, payload, sources=[telegram, vk])

    assert telegram_only == {TG_READ}
    assert mixed == {TG_READ, VK_READ, VK_SEARCH}


async def test_foreign_source_is_rejected_before_capability_derivation():
    with tenant_scope(7), pytest.raises(TenantContextError, match="foreign workspace"):
        await required_capabilities(None, {}, sources=[_source(1, 8, "vk")])


@pytest.mark.parametrize("scope", [None, 7])
async def test_missing_or_bypassed_workspace_scope_is_rejected(scope):
    with tenant_scope(scope, bypass=scope == 7), pytest.raises(TenantContextError, match="concrete workspace"):
        await required_capabilities(None, {}, sources=[])


async def test_discovery_without_persisted_sources_still_requires_declared_search_rights():
    payload = {
        "task_plan": {
            "platforms": ["vk"],
            "source_scope": "discovery",
            "collection": {
                "discovery": {"queries": ["design"], "communities": True},
            },
        },
        "collection": {
            "discovery": {"queries": ["design"], "communities": True},
        },
    }

    with tenant_scope(7):
        required = await required_capabilities(None, payload, sources=[])

    assert required == {VK_READ, VK_SEARCH}
