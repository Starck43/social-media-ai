"""Contract tests for messenger channels and digest broadcast (no real network).

Covers the provider rules the runtime depends on:
- Telegram Bot API: 4096-char limit, `parse_mode` only on the first chunk,
  `429 retry_after` handling, `getUpdates` offset bookkeeping.
- MAX Bot API: `platform-api2.max.ru`, `<access_token>` Authorization header,
  `chat_id` as a query param, 4000-char limit, marker-based long polling.
"""

from types import SimpleNamespace

import httpx
import pytest

from app.channels import max as max_module
from app.channels import registry as registry_module
from app.channels import telegram as tg_module
from app.channels.max import MAX_TEXT_LEN, MaxChannel
from app.channels.telegram import MAX_TEXT_LEN as TG_TEXT_LEN
from app.channels.telegram import TelegramChannel


class FakeResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """Stand-in for `httpx.AsyncClient`: records calls, replays scripted responses."""

    def __init__(self, responses: list[FakeResponse] | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    def _record(self, method: str, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise httpx.ConnectError("no scripted response left")
        return self.responses.pop(0)

    async def post(self, url: str, **kwargs) -> FakeResponse:
        return self._record("POST", url, **kwargs)

    async def get(self, url: str, **kwargs) -> FakeResponse:
        return self._record("GET", url, **kwargs)

    @property
    def posts(self) -> list[dict]:
        return [c for c in self.calls if c["method"] == "POST"]


@pytest.fixture
def fake_http(monkeypatch):
    """Patch httpx inside both channel modules with a scripted FakeClient."""

    def install(responses: list[FakeResponse] | None = None) -> FakeClient:
        client = FakeClient(responses)
        proxy = SimpleNamespace(AsyncClient=lambda *args, **kwargs: client, HTTPError=httpx.HTTPError)
        monkeypatch.setattr(tg_module, "httpx", proxy)
        monkeypatch.setattr(max_module, "httpx", proxy)
        return client

    return install


TG_UPDATE = {
    "update_id": 100,
    "message": {
        "message_id": 5,
        "from": {"id": 777},
        "chat": {"id": -100123, "type": "private"},
        "text": "hello",
    },
}


class TestTelegramChannel:
    async def test_send_disabled_without_token(self, fake_http, monkeypatch):
        monkeypatch.setattr(tg_module.settings, "TELEGRAM_BOT_TOKEN", "")
        client = fake_http()
        result = await TelegramChannel().send("1", "hi")
        assert result == {"success": False, "error": "Telegram token not configured"}
        assert client.calls == []

    async def test_send_payload(self, fake_http):
        client = fake_http([FakeResponse(200, {"ok": True, "result": {"message_id": 11}})])
        result = await TelegramChannel(token="TOK").send("-100123", "привет")
        assert result["success"] is True and result["message_id"] == 11
        call = client.posts[0]
        assert call["url"] == "https://api.telegram.org/botTOK/sendMessage"
        assert call["json"] == {
            "chat_id": "-100123",
            "text": "привет",
            "disable_web_page_preview": True,
            "parse_mode": "HTML",
        }

    async def test_send_splits_long_text_parse_mode_only_first(self, fake_http):
        client = fake_http(
            [
                FakeResponse(200, {"ok": True, "result": {"message_id": 1}}),
                FakeResponse(200, {"ok": True, "result": {"message_id": 2}}),
            ]
        )
        text = ("a" * (TG_TEXT_LEN - 10)) + "\n\n" + ("b" * 50)
        result = await TelegramChannel(token="TOK").send("1", text)
        assert len(client.posts) == 2
        assert len(client.posts[0]["json"]["text"]) <= TG_TEXT_LEN
        assert "parse_mode" in client.posts[0]["json"]
        assert "parse_mode" not in client.posts[1]["json"]
        assert result["message_ids"] == [1, 2] and result["message_id"] == 2

    async def test_send_retries_after_429(self, fake_http):
        client = fake_http(
            [
                FakeResponse(429, {"ok": False, "parameters": {"retry_after": 0}}),
                FakeResponse(200, {"ok": True, "result": {"message_id": 7}}),
            ]
        )
        result = await TelegramChannel(token="TOK").send("1", "hi")
        assert len(client.posts) == 2
        assert result["success"] is True and result["message_id"] == 7

    async def test_send_reports_api_error(self, fake_http):
        fake_http([FakeResponse(400, {"ok": False, "description": "chat not found"})])
        result = await TelegramChannel(token="TOK").send("1", "hi")
        assert result["success"] is False and result["error"] == "chat not found"

    async def test_poll_normalizes_and_advances_offset(self, fake_http):
        # update 100 → message, 101 → no text (skipped), 102 → message
        updates = [
            TG_UPDATE,
            {"update_id": 101, "message": {"chat": {"id": -100123}, "text": ""}},
            {
                "update_id": 102,
                "message": {"from": {"id": 888}, "chat": {"id": -100123}, "text": "world", "message_id": 6},
            },
        ]
        fake_http([FakeResponse(200, {"ok": True, "result": updates})])

        channel = TelegramChannel(token="TOK")
        agen = channel.poll()

        first = await anext(agen)
        assert first.channel == "telegram"
        assert first.chat_id == "-100123"
        assert first.user_id == "777"
        assert first.text == "hello"
        assert first.is_channel_post is False
        assert channel._offset == 101  # update_id + 1

        second = await anext(agen)  # text-less update 101 is skipped
        assert second.text == "world"
        assert second.user_id == "888"
        assert channel._offset == 103
        await agen.aclose()


class TestMaxChannel:
    async def test_send_disabled_without_token(self, fake_http, monkeypatch):
        monkeypatch.setattr(max_module.settings, "MAX_BOT_TOKEN", "")
        client = fake_http()
        result = await MaxChannel().send("1", "hi")
        assert result == {"success": False, "error": "MAX token not configured"}
        assert client.calls == []

    async def test_send_payload_and_auth_header(self, fake_http):
        client = fake_http([FakeResponse(200, {"message": {"body": {"mid": "mid-1"}}})])
        result = await MaxChannel(token="TOK", api_base="https://platform-api2.max.ru/").send("chat-9", "привет")
        assert result["success"] is True and result["message_id"] == "mid-1"
        call = client.posts[0]
        assert call["url"] == "https://platform-api2.max.ru/messages?chat_id=chat-9"
        assert call["headers"]["Authorization"] == "TOK"
        assert call["json"] == {"text": "привет", "notify": True, "format": "html"}

    async def test_send_splits_at_4000(self, fake_http):
        client = fake_http(
            [
                FakeResponse(200, {"message": {"body": {"mid": "1"}}}),
                FakeResponse(200, {"message": {"body": {"mid": "2"}}}),
            ]
        )
        text = ("x" * (MAX_TEXT_LEN - 5)) + "\n\n" + ("y" * 100)
        result = await MaxChannel(token="TOK").send("chat-9", text)
        assert len(client.posts) == 2
        assert all(len(c["json"]["text"]) <= MAX_TEXT_LEN for c in client.posts)
        assert "format" not in client.posts[1]["json"]
        assert result["message_ids"] == ["1", "2"]

    async def test_send_retries_after_429(self, fake_http):
        client = fake_http(
            [
                FakeResponse(429, {}, text="too many requests"),
                FakeResponse(200, {"message": {"body": {"mid": "ok"}}}),
            ]
        )
        result = await MaxChannel(token="TOK").send("chat-9", "hi")
        assert len(client.posts) == 2
        assert result["success"] is True and result["message_id"] == "ok"

    async def test_send_reports_http_error(self, fake_http):
        fake_http([FakeResponse(403, {}, text="forbidden")])
        result = await MaxChannel(token="TOK").send("chat-9", "hi")
        assert result["success"] is False and "403" in result["error"]

    async def test_poll_parses_updates_and_keeps_marker(self, fake_http):
        client = fake_http(
            [
                FakeResponse(
                    200,
                    {
                        "marker": 4242,
                        "updates": [
                            {"update_type": "message_callback"},
                            {
                                "update_type": "message_created",
                                "message": {
                                    "sender": {"user_id": 55},
                                    "recipient": {"chat_id": 900, "chat_type": "channel"},
                                    "body": {"text": "пост"},
                                },
                            },
                        ],
                    },
                )
            ]
        )
        agen = MaxChannel(token="TOK").poll()
        inbound = await anext(agen)
        await agen.aclose()

        assert inbound.channel == "max"
        assert inbound.chat_id == "900"
        assert inbound.user_id == "55"
        assert inbound.text == "пост"
        assert inbound.is_channel_post is True
        assert client.calls[0]["params"]["types"] == "message_created,message_edited"


class TestDigestBroadcast:
    async def test_sends_only_to_configured_targets(self, monkeypatch):
        sent: list[tuple[str, str]] = []

        class StubChannel:
            async def send(self, chat_id, text, parse_mode=None):
                sent.append((chat_id, text))
                return {"success": True, "message_id": 1}

        monkeypatch.setattr(registry_module, "get_channel", lambda name: StubChannel())
        monkeypatch.setattr(registry_module.settings, "TELEGRAM_DIGEST_CHANNEL_ID", "@my_channel")
        monkeypatch.setattr(registry_module.settings, "MAX_CHANNEL_ID", "")

        result = await registry_module.broadcast_digest("hi")
        assert list(result) == ["telegram"] and result["telegram"]["success"] is True
        assert sent == [("@my_channel", "hi")]

    async def test_channel_filter_and_empty_config(self, monkeypatch):
        sent: list[str] = []

        class StubChannel:
            async def send(self, chat_id, text, parse_mode=None):
                sent.append(text)
                return {"success": True}

        monkeypatch.setattr(registry_module, "get_channel", lambda name: StubChannel())
        monkeypatch.setattr(registry_module.settings, "TELEGRAM_DIGEST_CHANNEL_ID", "@my_channel")
        monkeypatch.setattr(registry_module.settings, "MAX_CHANNEL_ID", "")

        assert await registry_module.broadcast_digest("hi", channel_filter="max") == {}
        assert sent == []

        monkeypatch.setattr(registry_module.settings, "TELEGRAM_DIGEST_CHANNEL_ID", "")
        assert await registry_module.broadcast_digest("hi") == {}

    async def test_unconfigured_channel_is_reported(self, monkeypatch):
        monkeypatch.setattr(registry_module, "get_channel", lambda name: None)
        monkeypatch.setattr(registry_module.settings, "TELEGRAM_DIGEST_CHANNEL_ID", "@my_channel")
        monkeypatch.setattr(registry_module.settings, "MAX_CHANNEL_ID", "")

        result = await registry_module.broadcast_digest("hi")
        assert result["telegram"] == {"success": False, "error": "channel not configured"}
