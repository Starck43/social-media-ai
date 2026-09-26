"""Telegram push-ingest tests: mapping, watermark, unknown chats.

Telegram's Bot API hands channel posts to the bot as updates, so the listener
fans them into the analysis pipeline (`app/services/monitoring/ingest.py`).
The analyzer is stubbed — these tests cover routing, the watermark and skipping,
not the model.
"""

import pytest

from app.channels.base import Inbound
from app.services.monitoring import ingest as ingest_module


def _channel_post(message_id: int = 101, text: str = "hello", chat_id: str = "-100777") -> Inbound:
    return Inbound(
        channel="telegram",
        chat_id=chat_id,
        user_id=chat_id,
        text=text,
        is_channel_post=True,
        raw={
            "update_id": 1,
            "channel_post": {
                "message_id": message_id,
                "date": 1700000000,
                "text": text,
                "views": 42,
                "chat": {"id": int(chat_id)},
            },
        },
    )


@pytest.fixture
def stub_analyzer(monkeypatch):
    """Capture analysis calls instead of calling an LLM."""
    calls: list[list[dict]] = []

    class _FakeAnalytics:
        id = 999

    async def fake_analyze(self, content, source, **kwargs):
        calls.append(content)
        return _FakeAnalytics()

    monkeypatch.setattr("app.services.ai.analyzer.AIAnalyzer.base_analyze_content", fake_analyze)
    return calls


@pytest.fixture
async def telegram_source():
    """A monitored Telegram channel source, removed afterwards."""
    from app.models import Platform, Source
    from app.types import SourceType

    chat_id = "-100777"
    platform = await Platform.objects.filter(platform_type="telegram").first()
    assert platform is not None, "telegram platform row is missing — run `alembic upgrade head`"
    existing = await Source.objects.filter(platform_id=platform.id, external_id=chat_id).first()
    if existing is not None:
        await Source.objects.delete_by_id(existing.id)

    source = await Source.objects.create(
        platform_id=platform.id,
        source_type=SourceType.CHANNEL,
        external_id=chat_id,
        name="Test TG channel",
        is_active=True,
    )
    yield source
    await Source.objects.delete_by_id(source.id)


def test_normalize_channel_post_matches_content_contract():
    item = ingest_module.normalize_channel_post(_channel_post())

    assert item is not None
    assert item["id"] == "101"
    assert item["text"] == "hello"
    assert item["platform"] == "telegram"
    assert item["views"] == 42
    # Keys the analyzer's statistics rely on.
    for key in ("id", "external_id", "text", "date", "views", "reactions", "comments"):
        assert key in item


def test_normalize_skips_posts_without_text():
    inbound = _channel_post(text="")
    inbound.raw["channel_post"].pop("text")
    assert ingest_module.normalize_channel_post(inbound) is None


async def test_channel_post_is_ingested_and_watermarked(telegram_source, stub_analyzer):
    stored = await ingest_module.ingest_channel_post(_channel_post(message_id=101))

    assert stored is True
    assert len(stub_analyzer) == 1
    refreshed = await telegram_source.__class__.objects.get(id=telegram_source.id)
    assert refreshed is not None
    assert refreshed.last_item_id == "101"


async def test_replayed_post_is_skipped(telegram_source, stub_analyzer):
    await ingest_module.ingest_channel_post(_channel_post(message_id=101))
    stored = await ingest_module.ingest_channel_post(_channel_post(message_id=101))

    assert stored is False
    assert len(stub_analyzer) == 1


async def test_newer_post_passes_the_watermark(telegram_source, stub_analyzer):
    await ingest_module.ingest_channel_post(_channel_post(message_id=101))
    stored = await ingest_module.ingest_channel_post(_channel_post(message_id=102))

    assert stored is True
    assert len(stub_analyzer) == 2


async def test_unknown_chat_is_ignored(stub_analyzer):
    stored = await ingest_module.ingest_channel_post(_channel_post(chat_id="-100555"))

    assert stored is False
    assert stub_analyzer == []


async def test_private_message_is_ignored(stub_analyzer):
    inbound = _channel_post()
    inbound.is_channel_post = False

    assert await ingest_module.ingest_channel_post(inbound) is False
    assert stub_analyzer == []


@pytest.mark.tenancy
async def test_ingest_resolves_workspace_itself(stub_analyzer):
    """The listener has no workspace context, so ingest must establish one.

    `@pytest.mark.tenancy` keeps the conftest bypass switched off: without the
    self-resolution in `ingest_channel_post` this fails closed with
    TenantContextError instead of analysing the post.
    """
    from app.core.config import settings
    from app.core.tenant_context import tenant_scope
    from app.models import Platform, Source, Tenant
    from app.types import SourceType

    tenant = await Tenant.objects.filter(slug=settings.DEFAULT_TENANT_SLUG).first()
    assert tenant is not None
    platform = await Platform.objects.filter(platform_type="telegram").first()
    assert platform is not None

    chat_id = "-100888"
    with tenant_scope(tenant.id):
        existing = await Source.objects.filter(platform_id=platform.id, external_id=chat_id).first()
        if existing is not None:
            await Source.objects.delete_by_id(existing.id)
        source = await Source.objects.create(
            platform_id=platform.id,
            source_type=SourceType.CHANNEL,
            external_id=chat_id,
            name="Tenant resolution test",
            is_active=True,
        )

    try:
        assert await ingest_module.ingest_channel_post(_channel_post(message_id=201, chat_id=chat_id)) is True
        assert len(stub_analyzer) == 1
        with tenant_scope(tenant.id):
            refreshed = await Source.objects.get(id=source.id)
        assert refreshed is not None
        assert refreshed.last_item_id == "201"
    finally:
        with tenant_scope(tenant.id):
            await Source.objects.delete_by_id(source.id)


async def test_digest_target_channel_is_not_ingested(telegram_source, stub_analyzer):
    """The workspace's own digest must not re-enter the pipeline.

    When the digest-target chat is also monitored as a source, its channel
    posts arrive through the listener like any other — without the guard the
    analyzer would pay the LLM for our own digest text. The watermark must
    stay untouched so ordinary posts of that channel still ingest.
    """
    from app.models import TenantChannel

    chat_id = str(telegram_source.external_id)
    existing = await TenantChannel.objects.filter(channel="telegram", chat_id=chat_id).first()
    if existing is not None:
        await TenantChannel.objects.delete_by_id(existing.id)
    binding = await TenantChannel.objects.create(
        tenant_id=telegram_source.tenant_id,
        channel="telegram",
        chat_id=chat_id,
        kind="channel",
        is_digest_target=True,
    )

    try:
        stored = await ingest_module.ingest_channel_post(_channel_post(message_id=101))

        assert stored is False
        assert stub_analyzer == []
        refreshed = await type(telegram_source).objects.get(id=telegram_source.id)
        assert refreshed.last_item_id is None
    finally:
        await TenantChannel.objects.delete_by_id(binding.id)
