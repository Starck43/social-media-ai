"""Telegram L2 (MTProto) collection tests: mode switching, session errors, pull.

The L2 path talks to Telegram over MTProto via Telethon, which we never call in
tests. `load_session` and `build_client` are monkeypatched and the network client
is a fake, so these tests run without a Telegram server (the DB-backed ones use
Postgres, like the rest of the suite).

Covered: per-source L1/L2 mode switching, missing/expired sessions, entity
resolution, message normalization (cross-layer `external_id` for dedup), the
watermark advancing after a pull, and that collection is fail-closed under a
tenant scope.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.services.social.tg_session as tg_session_module
from app.services.social.credentials import AuthorizationRequired
from app.services.social.tg_client import TelegramClient


class _Platform:
    params = {}
    platform_type = None


def _source(**overrides) -> SimpleNamespace:
    """A Source-shaped stub for tests that do not touch the database."""
    defaults = dict(
        id=1,
        name="Test source",
        external_id="-100123",
        params={},
        last_item_id=None,
        last_checked=None,
        date_from=None,
        date_to=None,
        source_type="channel",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class _Msg:
    """Telethon Message look-alike (attribute contract only)."""

    def __init__(self, mid: int, text: str = "hello"):
        self.id = mid
        self.text = text
        self.message = text
        self.date = datetime(2026, 9, 25, tzinfo=timezone.utc)
        self.views = 10
        self.forwards = 2
        self.reactions = None
        self.replies = None
        self.chat_id = None
        self.peer_id = None
        self.sender_id = None
        self.pinned = False
        self.edit_date = None
        self.is_channel = True
        self.media = None


class _Reactions:
    def __init__(self, counts):
        self.results = [SimpleNamespace(count=c) for c in counts]


class _Replies:
    def __init__(self, count):
        self.replies = count


class _FakeClient:
    """In-memory Telethon client: authorized, one entity, fixed message list."""

    def __init__(self, messages=None, authorized=True, entity_error=None):
        self.messages = messages or [_Msg(12), _Msg(11)]
        self.authorized = authorized
        self.entity_error = entity_error
        self.kwargs = None
        self.disconnected = False

    async def connect(self):
        pass

    async def is_user_authorized(self):
        return self.authorized

    async def get_entity(self, ref):
        if self.entity_error:
            raise self.entity_error
        return ref

    async def disconnect(self):
        self.disconnected = True

    def iter_messages(self, entity, limit=None, min_id=0, min_date=None):
        self.kwargs = {"limit": limit, "min_id": min_id, "min_date": min_date}
        messages = [m for m in self.messages if m.id > min_id]

        async def gen():
            for m in messages:
                yield m

        return gen()


@pytest.fixture
def fake_client_factory(monkeypatch):
    """Patch the L2 seams; return the created fake client for assertions."""

    def _install(session=True, **client_kwargs):
        holder = {}

        async def fake_load(user_id=None):
            if not session:
                return None
            return tg_session_module.TelegramSession(api_id=12345, api_hash="h" * 32, session="s" * 20)

        def fake_build(session):
            client = _FakeClient(**client_kwargs)
            holder["client"] = client
            return client

        monkeypatch.setattr(tg_session_module, "load_session", fake_load)
        monkeypatch.setattr(tg_session_module, "build_client", fake_build)
        return holder

    return _install


# --------------------------------------------------------------------------- #
# Mode switching (no DB, no network)
# --------------------------------------------------------------------------- #


async def test_default_mode_is_auto_and_pull_is_noop_without_session(fake_client_factory):
    # `auto` with no MTProto session falls back to L1 (push) -> no items
    fake_client_factory(session=False)
    client = TelegramClient(_Platform())
    assert await client.collect_data(_source()) == []


async def test_api_mode_skips_pull():
    client = TelegramClient(_Platform())
    assert await client.collect_data(_source(params={"mode": "api"})) == []


async def test_auto_mode_pulls_via_l2_when_session_exists(fake_client_factory):
    fake_client_factory()
    client = TelegramClient(_Platform())
    items = await client.collect_data(_source(params={"mode": "auto"}))
    assert [int(i["id"]) for i in items] == [12, 11]


async def test_unknown_mode_falls_back_to_push():
    client = TelegramClient(_Platform())
    assert await client.collect_data(_source(params={"mode": "browser"})) == []


async def test_user_mode_without_session_raises_authorization_required(fake_client_factory):
    """L2 requested, no session → say so, do not fake an empty pull.

    An empty result reads as "nothing new in the channel", which is a very
    different fact from "we were never authorized to look".
    """
    fake_client_factory(session=False)
    client = TelegramClient(_Platform())
    with pytest.raises(AuthorizationRequired) as ei:
        await client.collect_data(_source(params={"mode": "user"}))

    assert "сессия" in str(ei.value).lower()
    assert ei.value.hint


# --------------------------------------------------------------------------- #
# Session unavailable / expired
# --------------------------------------------------------------------------- #


async def test_expired_or_revoked_session_raises_authorization_required(fake_client_factory):
    fake_client_factory(authorized=False)
    client = TelegramClient(_Platform())
    with pytest.raises(AuthorizationRequired):
        await client._collect_mtproto(_source(params={"mode": "user"}))


async def test_unresolvable_entity_returns_empty(fake_client_factory):
    fake_client_factory(entity_error=ValueError("bad peer"))
    client = TelegramClient(_Platform())
    assert await client._collect_mtproto(_source(params={"mode": "user"})) == []


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_watermark_id_parses_and_falls_back():
    assert TelegramClient._watermark_id(_source(last_item_id="42")) == 42
    assert TelegramClient._watermark_id(_source(last_item_id=None)) == 0
    assert TelegramClient._watermark_id(_source(last_item_id="garbage")) == 0


def test_pull_start_date_priority():
    client = TelegramClient(_Platform())
    # force_refresh prefers cli_dates.start_date
    src = _source(
        params={"force_refresh": True, "cli_dates": {"start_date": "2026-09-01"}},
        last_checked=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    start = client._pull_start_date(src)
    assert start is not None and start.year == 2026 and start.month == 9 and start.day == 1
    # otherwise last_checked
    src2 = _source(last_checked=datetime(2026, 9, 10, tzinfo=timezone.utc))
    assert client._pull_start_date(src2).day == 10
    # nothing configured -> None
    assert client._pull_start_date(_source()) is None


def test_entity_ref_maps_bot_api_ids_to_peers():
    from telethon.tl.types import PeerChannel, PeerChat, PeerUser

    assert isinstance(TelegramClient._entity_ref("-1001234567890"), PeerChannel)
    assert isinstance(TelegramClient._entity_ref("-12345"), PeerChat)
    assert isinstance(TelegramClient._entity_ref("12345"), PeerUser)
    assert TelegramClient._entity_ref("@channel") == "@channel"
    assert TelegramClient._entity_ref("channel") == "@channel"
    assert TelegramClient._entity_ref("https://t.me/channel") == "@channel"


def test_normalize_message_matches_content_contract():
    client = TelegramClient(_Platform())
    msg = _Msg(42)
    msg.views = 10
    msg.forwards = 2
    msg.reactions = _Reactions([3, 2])
    msg.replies = _Replies(5)
    msg.media = SimpleNamespace(__class__=SimpleNamespace(__name__="MessageMediaPhoto"))

    item = client._normalize_message(msg, _source(external_id="-100777"))

    assert item is not None
    assert item["id"] == "42"
    assert item["external_id"] == "-100777_42"  # same key as the push path -> dedup-safe
    assert item["text"] == "hello"
    assert item["platform"] == "telegram"
    assert item["views"] == 10
    assert item["reactions"] == 5
    assert item["comments"] == 5
    assert item["message_type"] == "post"
    assert item["has_media"] is True
    for key in ("id", "external_id", "text", "date", "views", "reactions", "comments"):
        assert key in item


def test_normalize_skips_media_only_messages():
    client = TelegramClient(_Platform())
    msg = _Msg(42, text=None)
    assert client._normalize_message(msg, _source()) is None


# --------------------------------------------------------------------------- #
# Full L2 pull against a real source row (Postgres)
# --------------------------------------------------------------------------- #


@pytest.fixture
async def telegram_user_source():
    """A monitored Telegram channel source in `user` mode, removed afterwards."""
    from app.models import Platform, Source
    from app.types import SourceType

    chat_id = "-100701"
    platform = await Platform.objects.filter(platform_type="telegram").first()
    assert platform is not None, "telegram platform row is missing — run `alembic upgrade head`"
    existing = await Source.objects.filter(platform_id=platform.id, external_id=chat_id).first()
    if existing is not None:
        await Source.objects.delete_by_id(existing.id)

    source = await Source.objects.create(
        platform_id=platform.id,
        source_type=SourceType.CHANNEL,
        external_id=chat_id,
        name="Test TG L2 source",
        params={"mode": "user"},
        last_item_id="10",
        is_active=True,
    )
    yield source
    await Source.objects.delete_by_id(source.id)


async def test_l2_pull_fetches_new_messages_and_advances_watermark(telegram_user_source, fake_client_factory):
    holder = fake_client_factory()
    client = TelegramClient(_Platform())

    items = await client._collect_mtproto(telegram_user_source)

    assert [int(i["id"]) for i in items] == [12, 11]
    assert holder["client"].kwargs["min_id"] == 10  # watermark passed as cursor
    refreshed = await telegram_user_source.__class__.objects.get(id=telegram_user_source.id)
    assert refreshed is not None
    assert refreshed.last_item_id == "12"  # watermark advanced past the newest


@pytest.mark.tenancy
async def test_l2_pull_is_tenant_fail_closed(monkeypatch):
    """Without a workspace scope the source row must not be reached by accident.

    `@pytest.mark.tenancy` keeps the bypass off, so the pull must run inside the
    owning workspace — exactly like ingest and the job dispatcher.
    """
    from app.core.config import settings
    from app.core.tenant_context import tenant_scope
    from app.models import Platform, Source, Tenant
    from app.types import SourceType

    tenant = await Tenant.objects.filter(slug=settings.DEFAULT_TENANT_SLUG).first()
    assert tenant is not None
    platform = await Platform.objects.filter(platform_type="telegram").first()
    assert platform is not None

    chat_id = "-100702"
    with tenant_scope(tenant.id):
        existing = await Source.objects.filter(platform_id=platform.id, external_id=chat_id).first()
        if existing is not None:
            await Source.objects.delete_by_id(existing.id)
        source = await Source.objects.create(
            platform_id=platform.id,
            source_type=SourceType.CHANNEL,
            external_id=chat_id,
            name="Tenant L2 test",
            params={"mode": "user"},
            last_item_id="5",
            is_active=True,
        )

    async def fake_load(user_id=None):
        from app.core.tenant_context import current_tenant_id

        # The caller passes no explicit owner; resolution must use the ambient
        # workspace scope, exactly like the job dispatcher.
        assert current_tenant_id() == tenant.id, "L2 session must resolve in the owning workspace"
        return tg_session_module.TelegramSession(api_id=12345, api_hash="h" * 32, session="s" * 20)

    def fake_build(session):
        return _FakeClient(messages=[_Msg(7)])

    monkeypatch.setattr(tg_session_module, "load_session", fake_load)
    monkeypatch.setattr(tg_session_module, "build_client", fake_build)

    try:
        with tenant_scope(tenant.id):
            client = TelegramClient(_Platform())
            items = await client._collect_mtproto(source)
            assert [int(i["id"]) for i in items] == [7]
            refreshed = await Source.objects.get(id=source.id)
            assert refreshed is not None
            assert refreshed.last_item_id == "7"
    finally:
        with tenant_scope(tenant.id):
            await Source.objects.delete_by_id(source.id)
