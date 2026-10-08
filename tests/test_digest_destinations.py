"""Tenant-safe delivery regressions with real binding queries, no network/LLM."""

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.channels import registry
from app.core.config import settings
from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass, tenant_scope
from app.models import Tenant, TenantChannel
from app.models.managers.tenant_manager import tenant_channels
from app.services.digest import builder

pytestmark = pytest.mark.tenancy


@pytest.fixture
async def workspaces():
    owner = await Tenant.objects.get(slug=settings.DEFAULT_TENANT_SLUG)
    others = [
        await Tenant.objects.create(name="Digest isolation", slug=f"digest-{uuid4().hex}", plan="business")
        for _ in range(2)
    ]
    bindings = []

    async def bind(tenant, channel="telegram", chat_id=None, **flags):
        row = await TenantChannel.objects.create(
            tenant_id=tenant.id,
            channel=channel,
            chat_id=chat_id or f"test-{uuid4().hex}",
            is_active=flags.get("is_active", True),
            is_digest_target=flags.get("is_digest_target", True),
        )
        bindings.append(row)
        return row

    try:
        yield owner, *others, bind
    finally:
        for binding in bindings:
            await TenantChannel.objects.delete_by_id(binding.id)
        for tenant in others:
            await Tenant.objects.delete_by_id(tenant.id)


@pytest.fixture
def sent(monkeypatch):
    calls = []

    class StubChannel:
        def __init__(self, channel):
            self.channel = channel

        async def send(self, chat_id, text, parse_mode=None):
            calls.append((self.channel, chat_id, text, parse_mode))
            return {"success": True, "message_id": len(calls)}

    monkeypatch.setattr(registry, "get_channel", lambda name: StubChannel(name))
    monkeypatch.setattr(settings, "TELEGRAM_DIGEST_CHANNEL_ID", "")
    monkeypatch.setattr(settings, "MAX_CHANNEL_ID", "")
    return calls


async def test_clients_never_receive_or_send_to_legacy_or_other_tenant(workspaces, sent, monkeypatch):
    owner, first, second, bind = workspaces
    legacy = await bind(owner)
    a = await bind(first)
    b = await bind(second)
    monkeypatch.setattr(settings, "TELEGRAM_DIGEST_CHANNEL_ID", legacy.chat_id)
    monkeypatch.setattr(settings, "MAX_CHANNEL_ID", "unbound-global-max")

    with tenant_scope(first.id):
        results = await registry.broadcast_digest("first tenant")
    assert list(results) == [f"telegram:{a.chat_id}"]
    with tenant_scope(second.id):
        results = await registry.broadcast_digest("second tenant")
    assert list(results) == [f"telegram:{b.chat_id}"]
    assert sent == [
        ("telegram", a.chat_id, "first tenant", "HTML"),
        ("telegram", b.chat_id, "second tenant", "HTML"),
    ]


@pytest.mark.parametrize("channel,env_name", [("telegram", "TELEGRAM_DIGEST_CHANNEL_ID"), ("max", "MAX_CHANNEL_ID")])
async def test_bootstrap_matching_env_does_not_change_bound_delivery(workspaces, sent, monkeypatch, channel, env_name):
    owner, _, _, bind = workspaces
    binding = await bind(owner, channel=channel, chat_id=f"@digest_{uuid4().hex}")
    monkeypatch.setattr(settings, env_name, f" {binding.chat_id.upper()} ")
    with tenant_scope(owner.id):
        results = await registry.broadcast_digest("owner")
    assert list(results) == [f"{channel}:{binding.chat_id}"]
    assert sent == [(channel, binding.chat_id, "owner", "HTML")]
    monkeypatch.setattr(settings, env_name, "unrelated-global-address")
    with tenant_scope(owner.id):
        assert list(await registry.broadcast_digest("owner again")) == list(results)
    assert sent[-1] == (channel, binding.chat_id, "owner again", "HTML")


async def test_unbound_legacy_address_is_not_a_recipient(workspaces, sent, monkeypatch):
    owner, _, _, _ = workspaces
    monkeypatch.setattr(settings, "TELEGRAM_DIGEST_CHANNEL_ID", "not-bound")
    monkeypatch.setattr(settings, "MAX_CHANNEL_ID", "also-not-bound")
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "admin-is-not-a-digest-target")
    with tenant_scope(owner.id):
        assert await registry.broadcast_digest("private") == {}
    assert sent == []


async def test_bootstrap_env_cannot_target_a_foreign_binding(workspaces, sent, monkeypatch):
    owner, first, _, bind = workspaces
    foreign = await bind(first)
    monkeypatch.setattr(settings, "TELEGRAM_DIGEST_CHANNEL_ID", foreign.chat_id)
    with tenant_scope(owner.id):
        assert await registry.broadcast_digest("owner private") == {}
    assert sent == []


@pytest.mark.parametrize("flags", [{"is_active": False}, {"is_digest_target": False}])
async def test_legacy_does_not_resurrect_disabled_binding(workspaces, sent, monkeypatch, flags):
    owner, _, _, bind = workspaces
    binding = await bind(owner, **flags)
    monkeypatch.setattr(settings, "TELEGRAM_DIGEST_CHANNEL_ID", binding.chat_id)
    with tenant_scope(owner.id):
        assert await registry.broadcast_digest("private") == {}
    assert sent == []


async def test_explicit_foreign_override_is_denied_before_target_lookup(workspaces, sent, monkeypatch):
    _, first, second, _ = workspaces

    async def unexpected_lookup(_tenant_id):
        pytest.fail("Foreign override must be rejected before target lookup")

    monkeypatch.setattr(tenant_channels, "digest_targets", unexpected_lookup)
    with tenant_scope(first.id):
        with pytest.raises(TenantContextError, match="foreign overrides"):
            await registry.broadcast_digest("private", tenant_id=second.id)
    assert sent == []


async def test_explicit_current_workspace_is_allowed(workspaces, sent):
    _, first, _, bind = workspaces
    binding = await bind(first)
    with tenant_scope(first.id):
        assert await registry.broadcast_digest("private", tenant_id=first.id)
    assert sent == [("telegram", binding.chat_id, "private", "HTML")]


@pytest.mark.parametrize("explicit", [False, True])
async def test_unscoped_non_operator_fails_closed(workspaces, sent, explicit):
    owner, _, _, _ = workspaces
    with tenant_scope():
        with pytest.raises(TenantContextError):
            await registry.broadcast_digest("private", tenant_id=owner.id if explicit else None)
    assert sent == []


@pytest.mark.parametrize("kind", ["missing", "inactive"])
async def test_operator_cannot_send_for_invalid_workspace(workspaces, sent, kind):
    _, first, _, _ = workspaces
    if kind == "inactive":
        await Tenant.objects.update_by_id(first.id, is_active=False)
        tenant_id = first.id
    else:
        tenant_id = 2147483647
    with tenant_scope(bypass=True):
        with pytest.raises(TenantContextError, match="missing or inactive"):
            await registry.broadcast_digest("private", tenant_id=tenant_id)
    assert sent == []


async def test_filter_applies_to_bound_targets_not_only_env(workspaces, sent):
    _, first, _, bind = workspaces
    await bind(first, "telegram")
    target = await bind(first, "max")
    with tenant_scope(first.id):
        results = await registry.broadcast_digest("private", channel_filter="max")
    assert list(results) == [f"max:{target.chat_id}"]
    assert sent == [("max", target.chat_id, "private", "HTML")]


async def test_destination_dedup_and_defensive_binding_checks(workspaces, sent, monkeypatch):
    _, first, second, _ = workspaces
    common = dict(tenant_id=first.id, channel="telegram", is_active=True, is_digest_target=True)
    rows = [
        SimpleNamespace(**common, chat_id="@Same"),
        SimpleNamespace(**common, chat_id=" @same "),
        SimpleNamespace(**{**common, "tenant_id": second.id}, chat_id="foreign"),
        SimpleNamespace(**{**common, "is_active": False}, chat_id="inactive"),
        SimpleNamespace(**{**common, "is_digest_target": False}, chat_id="not-digest"),
        SimpleNamespace(**common, chat_id=" "),
    ]

    async def targets(tenant_id):
        assert tenant_id == first.id
        return rows

    monkeypatch.setattr(tenant_channels, "digest_targets", targets)
    with tenant_scope(first.id):
        results = await registry.broadcast_digest("private")
    assert list(results) == ["telegram:@same"]
    assert sent == [("telegram", "@same", "private", "HTML")]


async def test_same_identifier_on_different_transports_is_not_deduplicated(workspaces, sent):
    _, first, _, bind = workspaces
    chat_id = f"shared-{uuid4().hex}"
    await bind(first, "telegram", chat_id)
    await bind(first, "max", chat_id)
    with tenant_scope(first.id):
        results = await registry.broadcast_digest("private")
    assert set(results) == {f"telegram:{chat_id}", f"max:{chat_id}"}
    assert {c[0] for c in sent} == {"telegram", "max"}


async def test_unconfigured_transport_is_reported_for_owned_binding(workspaces, sent, monkeypatch):
    _, first, _, bind = workspaces
    target = await bind(first)
    monkeypatch.setattr(registry, "get_channel", lambda _: None)
    with tenant_scope(first.id):
        results = await registry.broadcast_digest("private")
    assert results == {f"telegram:{target.chat_id}": {"success": False, "error": "channel not configured"}}
    assert sent == []


async def test_operator_manual_builder_scopes_reads_and_send_to_bootstrap(workspaces, sent, monkeypatch):
    owner, _, _, _ = workspaces
    visited = []

    async def scoped_run(*args):
        visited.append((current_tenant_id(), is_bypass()))
        return {"status": "skipped"}

    monkeypatch.setattr(builder, "_build_and_publish_scoped", scoped_run)
    with tenant_scope(bypass=True):
        assert (await builder.build_and_publish())["status"] == "skipped"
        assert current_tenant_id() is None and is_bypass()
    assert visited == [(owner.id, False)]
    assert sent == []


async def test_builder_removes_bypass_even_when_workspace_is_selected(workspaces, sent, monkeypatch):
    _, first, _, _ = workspaces
    visited = []

    async def scoped_run(*args):
        visited.append((current_tenant_id(), is_bypass()))
        return {"status": "skipped"}

    monkeypatch.setattr(builder, "_build_and_publish_scoped", scoped_run)
    with tenant_scope(first.id, bypass=True):
        await builder.build_and_publish()
        assert current_tenant_id() == first.id and is_bypass()
    assert visited == [(first.id, False)]


@pytest.mark.parametrize("select_client", [False, True])
async def test_real_manual_run_keeps_analytics_run_and_delivery_in_one_workspace(
    workspaces, sent, monkeypatch, select_client
):
    """Real ORM reads/run writes must not inherit the caller's operator bypass."""
    from app.models import AIAnalytics, DigestRun, Platform, Source
    from app.types import SourceType

    owner, first, second, bind = workspaces
    selected = first if select_client else owner
    recipient = await bind(selected)
    platform = await Platform.objects.filter(platform_type="vk").first()
    sources = []
    marker = f"scope-{uuid4().hex}"
    run_id = None
    for tenant in (owner, first, second):
        with tenant_scope(tenant.id):
            source = await Source.objects.create(
                platform_id=platform.id,
                name="Digest scope fixture",
                source_type=SourceType.GROUP,
                external_id=uuid4().hex,
            )
            sources.append((tenant.id, source.id))
            await AIAnalytics.objects.create(source_id=source.id, summary_data={"marker": f"{marker}-{tenant.id}"})

    async def scoped_aggregate(*args):
        assert current_tenant_id() == selected.id and not is_bypass()
        rows = await AIAnalytics.objects.all()
        markers = [
            row.summary_data["marker"] for row in rows if (row.summary_data or {}).get("marker", "").startswith(marker)
        ]
        assert markers == [f"{marker}-{selected.id}"]
        today = date.today()
        return (
            {
                "title": "Fixture digest",
                "brief": markers[0],
                "period_start": today,
                "period_end": today,
            },
            today,
            today,
        )

    async def no_llm(_data):
        return None, {"model": None}

    monkeypatch.setattr(builder, "aggregate", scoped_aggregate)
    monkeypatch.setattr(builder, "_summarize", no_llm)
    try:
        with tenant_scope(selected.id if select_client else None, bypass=True):
            result = await builder.build_and_publish()
        assert result["status"] == "sent"
        assert len(sent) == 1 and sent[0][:2] == ("telegram", recipient.chat_id)
        assert f"{marker}-{selected.id}" in sent[0][2]
        with tenant_scope(selected.id):
            run = (await DigestRun.objects.order_by(DigestRun.id.desc()).limit(1))[0]
            run_id = run.id
            assert run.tenant_id == selected.id and run.status == "sent"
    finally:
        with tenant_scope(selected.id):
            if run_id is not None:
                await DigestRun.objects.delete_by_id(run_id)
        for tenant_id, source_id in sources:
            with tenant_scope(tenant_id):
                await Source.objects.delete_by_id(source_id)
