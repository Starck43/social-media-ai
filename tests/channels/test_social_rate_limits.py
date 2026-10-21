"""Platform rate limits during collection: bounded pause, then an honest deferral.

No network, database or Telegram server: the request boundary, session seams
and job lookups are stubbed. Run with
`.venv/bin/python -m pytest -o addopts='' --noconftest tests/test_social_rate_limits.py -q`.
"""

from types import SimpleNamespace

import httpx
import pytest

import app.services.social.base as base_module
import app.services.social.tg_session as tg_session_module
from app.core.config import settings
from app.services.social.base import BaseClient
from app.services.social.rate_limit import SourceRateLimited, next_pause, response_rate_limit
from app.types import SourceType


class _Platform:
    platform_type = "vk"
    params = {}


class _Source:
    id = 7
    source_type = SourceType.USER
    external_id = "owner"
    name = "wall"
    params = {"incremental_mode": False}


class _ScriptedClient(BaseClient):
    """Returns (or raises) one scripted response per request."""

    def __init__(self, script):
        super().__init__(_Platform())
        self.script = list(script)
        self.calls = 0

    async def _make_request(self, method, params):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def _extract_items_from_response(self, response):
        return response.get("response", {}).get("items", [])

    def _should_stop_pagination(self, response, items, page_size, total_collected):
        return True

    def _build_paginated_response(self, all_items):
        return {"response": {"items": all_items}}

    def _normalize_response(self, response, source_type):
        return response["response"]["items"]

    def _get_api_method(self, source_type, content_type):
        return "wall.get"

    def _build_params(self, source, method):
        return {}


VK_TOO_MANY = {"error": {"error_code": 6, "error_msg": "Too many requests per second"}}
VK_QUOTA = {"error": {"error_code": 29, "error_msg": "Rate limit reached"}}
VK_FLOOD = {"error": {"error_code": 9, "error_msg": "Flood control"}}
PAGE = {"response": {"items": [{"id": 1}]}}


@pytest.fixture
def pauses(monkeypatch):
    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(base_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(settings, "SOCIAL_RATE_LIMIT_RETRIES", 2)
    monkeypatch.setattr(settings, "SOCIAL_RATE_LIMIT_MAX_WAIT", 10.0)
    return recorded


def _http_429(retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    request = httpx.Request("GET", "https://api.example/wall.get")
    response = httpx.Response(429, headers=headers, request=request)
    return httpx.HTTPStatusError("429", request=request, response=response)


async def test_short_vk_limit_is_waited_out_and_retried(pauses):
    client = _ScriptedClient([VK_TOO_MANY, PAGE])
    assert await client.collect_data(_Source()) == [{"id": 1}]
    assert client.calls == 2
    # One bounded rate-limit pause before the retry (the 0.1 s page pacing does
    # not run: the single page ends pagination).
    assert pauses == [1.0]


async def test_persistent_vk_limit_defers_after_bounded_retries(pauses):
    client = _ScriptedClient([VK_TOO_MANY, VK_TOO_MANY, VK_TOO_MANY])
    with pytest.raises(SourceRateLimited) as info:
        await client.collect_data(_Source())
    assert client.calls == 3
    assert pauses == [1.0, 2.0]
    assert info.value.platform == "vk"


@pytest.mark.parametrize("body", [VK_QUOTA, VK_FLOOD])
async def test_vk_quota_and_flood_control_defer_without_waiting(pauses, body):
    client = _ScriptedClient([body])
    with pytest.raises(SourceRateLimited):
        await client.collect_data(_Source())
    assert client.calls == 1
    assert pauses == []


async def test_incremental_mode_uses_the_same_policy(pauses):
    source = _Source()
    source.params = {"incremental_mode": True}
    client = _ScriptedClient([VK_TOO_MANY, PAGE])

    import app.services.checkpoint_manager as checkpoint_module

    original = checkpoint_module.CheckpointManager.get_collection_params
    checkpoint_module.CheckpointManager.get_collection_params = staticmethod(lambda src: {})
    try:
        assert await client.collect_data(source) == [{"id": 1}]
    finally:
        checkpoint_module.CheckpointManager.get_collection_params = original
    assert pauses == [1.0]


async def test_http_429_honours_a_small_retry_after(pauses):
    client = _ScriptedClient([_http_429(3), PAGE])
    assert await client.collect_data(_Source()) == [{"id": 1}]
    assert pauses == [3.0]


async def test_http_429_with_a_long_retry_after_defers_at_once(pauses):
    client = _ScriptedClient([_http_429(120)])
    with pytest.raises(SourceRateLimited) as info:
        await client.collect_data(_Source())
    assert pauses == []
    assert info.value.retry_after == 120.0


async def test_other_http_errors_are_not_rate_limits(pauses):
    request = httpx.Request("GET", "https://api.example/wall.get")
    error = httpx.HTTPStatusError("500", request=request, response=httpx.Response(500, request=request))
    client = _ScriptedClient([error])
    with pytest.raises(httpx.HTTPStatusError):
        await client.collect_data(_Source())
    assert pauses == []


async def test_other_api_errors_keep_their_error_semantics(pauses):
    client = _ScriptedClient([{"error": {"error_code": 15, "error_msg": "Access denied"}}])
    with pytest.raises(RuntimeError, match=r"Ошибка API \(15\)"):
        await client.collect_data(_Source())
    assert pauses == []


def test_rate_limit_is_not_a_runtime_error():
    # VK layer fallback and token refresh catch RuntimeError; a rate limit must
    # not make them switch credentials and hit the same limit again.
    assert not issubclass(SourceRateLimited, RuntimeError)


def test_policy_bounds(monkeypatch):
    monkeypatch.setattr(settings, "SOCIAL_RATE_LIMIT_RETRIES", 1)
    monkeypatch.setattr(settings, "SOCIAL_RATE_LIMIT_MAX_WAIT", 5.0)
    assert next_pause(0, True, None) == 1.0
    assert next_pause(1, True, None) is None  # retry budget spent
    assert next_pause(0, True, 6.0) is None  # hint above the cap
    assert next_pause(0, False, None) is None  # not retryable
    monkeypatch.setattr(settings, "SOCIAL_RATE_LIMIT_RETRIES", 0)
    assert next_pause(0, True, None) is None
    assert response_rate_limit({"ok": False, "error_code": 429, "parameters": {"retry_after": 2}}) == (True, 2.0)
    assert response_rate_limit({"response": {"items": []}}) is None
    assert response_rate_limit({"error": {"error_code": True}}) is None


# --------------------------------------------------------------------------- #
# Telegram L2: FloodWaitError beyond Telethon's own bounded sleep


class FloodWaitError(Exception):
    """Name-compatible stand-in for telethon.errors.FloodWaitError."""

    def __init__(self, seconds):
        super().__init__(f"A wait of {seconds} seconds is required")
        self.seconds = seconds


class FloodPremiumWaitError(FloodWaitError):
    pass


class _FloodingTelethon:
    def __init__(self, error):
        self.error = error
        self.disconnected = False
        self.flood_sleep_threshold = 60

    async def connect(self):
        pass

    async def is_user_authorized(self):
        return True

    async def get_entity(self, ref):
        return ref

    async def disconnect(self):
        self.disconnected = True

    def iter_messages(self, entity, **kwargs):
        error = self.error

        async def gen():
            yield SimpleNamespace(id=1)
            raise error

        return gen()


@pytest.fixture
def flooding_session(monkeypatch):
    holder = {}

    async def fake_load(user_id=None):
        return tg_session_module.TelegramSession(api_id=1, api_hash="h" * 32, session="s" * 20)

    def install(error):
        def fake_build(session):
            holder["client"] = _FloodingTelethon(error)
            return holder["client"]

        monkeypatch.setattr(tg_session_module, "build_client", fake_build)
        return holder

    async def fake_owner(source):
        return None

    import app.services.social.owner as owner_module

    monkeypatch.setattr(tg_session_module, "load_session", fake_load)
    monkeypatch.setattr(owner_module, "resolve_source_owner", fake_owner)
    monkeypatch.setattr(settings, "SOCIAL_RATE_LIMIT_MAX_WAIT", 10.0)
    return install


def _tg_source():
    return SimpleNamespace(
        id=3,
        name="chat",
        external_id="-100123",
        params={"mode": "user"},
        last_item_id=None,
        last_checked=None,
        date_from=None,
        date_to=None,
        source_type="channel",
    )


@pytest.mark.parametrize("error", [FloodWaitError(120), FloodPremiumWaitError(45)])
async def test_telegram_flood_wait_defers_instead_of_reading_as_empty(flooding_session, error):
    from app.services.social.tg_client import TelegramClient

    holder = flooding_session(error)
    with pytest.raises(SourceRateLimited) as info:
        await TelegramClient(platform=SimpleNamespace(params={}, platform_type=None))._collect_mtproto(_tg_source())
    assert info.value.platform == "telegram"
    assert info.value.retry_after == float(error.seconds)
    # Telethon's own automatic sleep is capped by the same bound.
    assert holder["client"].flood_sleep_threshold == 10
    assert holder["client"].disconnected is True


async def test_other_telegram_errors_keep_the_empty_pull(flooding_session):
    from app.services.social.tg_client import TelegramClient

    flooding_session(ConnectionError("network down"))
    result = await TelegramClient(platform=SimpleNamespace(params={}, platform_type=None))._collect_mtproto(
        _tg_source()
    )
    assert result == []


# --------------------------------------------------------------------------- #
# Job outcome and user-facing summaries


@pytest.fixture
def stub_collect_job(monkeypatch):
    import app.jobs.handlers as handlers

    async def fake_load_task(task_id):
        return None

    def install(sources, behaviour):
        async def fake_resolve(task, payload=None):
            return sources

        async def fake_collect(self, source, analyze=True, content_type="posts", force_reanalyze=False, run_id=None, **kwargs):
            return await behaviour(source)

        monkeypatch.setattr(handlers, "_load_task", fake_load_task)
        monkeypatch.setattr(handlers, "_resolve_sources", fake_resolve)
        monkeypatch.setattr("app.services.monitoring.collector.ContentCollector.__init__", lambda self: None)
        monkeypatch.setattr("app.services.monitoring.collector.ContentCollector.collect_from_source", fake_collect)

    return install


def _job_source(source_id, name):
    return SimpleNamespace(id=source_id, name=name, external_id=name, params={})


async def test_collect_job_records_a_deferred_source_apart_from_errors(stub_collect_job):
    from app.jobs.dispatcher import _job_success_message
    from app.jobs.handlers import handle_collect
    from app.web.tasks import _job_summary, _source_links

    async def behaviour(source):
        if source.id == 2:
            raise SourceRateLimited("vk", 30.4)
        return {"content_count": 2, "new_items": 2, "content_hashes": ["a", "b"]}

    stub_collect_job([_job_source(1, "alpha"), _job_source(2, "beta")], behaviour)
    stats = await handle_collect({"job_id": 5})

    assert stats["error"] == 0
    assert stats["empty"] == 0
    assert stats["rate_limited"] == 1
    assert stats["rate_limited_sources"] == ["beta"]
    deferred = stats["per_source"][1]
    assert deferred["outcome"] == "rate_limited"
    assert deferred["retry_after"] == 31
    assert "error" not in deferred

    job = SimpleNamespace(id=5, job_type="collect", result=stats, status="done")
    message = _job_success_message(job, stats)
    assert "успешно" not in message
    assert "частично" in message
    assert "beta" in message

    summary = _job_summary(job)
    assert summary["outcome"] == "partial"
    assert any(stat["label"] == "отложено (лимит запросов)" for stat in summary["stats"])

    links = {link["name"]: link for link in _source_links(job)}
    assert links["beta"]["note"] == "отложено: лимит запросов"
    assert links["beta"]["error"] is False
