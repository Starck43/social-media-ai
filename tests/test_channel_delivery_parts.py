"""Single-part contract tests: no live HTTP, sleep, hidden retry or splitting."""

import httpx
import pytest

from app.channels import max as max_module
from app.channels import telegram as tg_module
from app.channels.max import MaxChannel
from app.channels.telegram import TelegramChannel


class Response:
    def __init__(self, status, data):
        self.status_code, self.data = status, data

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class Client:
    def __init__(self, response):
        self.response, self.calls = response, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture(params=["telegram", "max"])
def transport(request, monkeypatch):
    name = request.param
    module = tg_module if name == "telegram" else max_module
    channel = TelegramChannel(token="private-token") if name == "telegram" else MaxChannel(token="private-token")

    def install(response):
        client = Client(response)
        monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: client)
        return client

    return name, channel, install


async def test_exact_part_and_html_on_every_call(transport):
    name, channel, install = transport
    payload = {"ok": True, "result": {"message_id": 7}} if name == "telegram" else {"message": {"body": {"mid": "7"}}}
    client = install(Response(200, payload))
    text = "<b>part</b>\n\n exact trailing newline\n"
    for _ in range(2):
        assert await channel.send_part("-100", text) == {"success": True, "outcome": "sent", "message_id": "7"}
    assert len(client.calls) == 2
    for url, kwargs in client.calls:
        assert kwargs["json"]["text"] == text
        if name == "telegram":
            assert kwargs["json"]["parse_mode"] == "HTML"
            assert kwargs["json"]["chat_id"] == "-100"
        else:
            assert kwargs["json"]["format"] == "html"
            assert kwargs["params"] == {"chat_id": "-100"}
            assert kwargs["headers"]["Authorization"] == "private-token"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422, 429])
async def test_known_rejection_never_retries_or_returns_raw_description(transport, status):
    name, channel, install = transport
    client = install(Response(status, {"ok": False, "error_code": status, "description": "private-provider-detail"}))
    result = await channel.send_part("1", "part")
    assert result == {"success": False, "outcome": "rejected", "error_code": f"http_{status}"}
    assert len(client.calls) == 1
    assert "private" not in str(result)


@pytest.mark.parametrize("status", [408, 409, 500, 502, 503, 302])
async def test_ambiguous_http_is_not_known_unsent(transport, status):
    _, channel, install = transport
    client = install(Response(status, {"ok": False, "error_code": status}))
    assert (await channel.send_part("1", "part"))["outcome"] == "uncertain"
    assert len(client.calls) == 1


@pytest.mark.parametrize("data", [None, [], {}, ValueError("private-response"), {"ok": True}, {"message": []}])
async def test_malformed_success_is_uncertain(transport, data):
    _, channel, install = transport
    client = install(Response(200, data))
    result = await channel.send_part("1", "part")
    assert result["success"] is False and result["outcome"] == "uncertain"
    assert len(client.calls) == 1 and "private" not in str(result)


@pytest.mark.parametrize("message_id", [None, True, False, 0, -1, {}, [], "", " "])
async def test_invalid_acknowledgement_is_uncertain(transport, message_id):
    name, channel, install = transport
    data = {"ok": True, "result": {"message_id": message_id}} if name == "telegram" else {"body": {"mid": message_id}}
    install(Response(200, data))
    assert (await channel.send_part("1", "part"))["outcome"] == "uncertain"


async def test_timeout_is_uncertain_and_redacted(transport):
    _, channel, install = transport
    client = install(httpx.ReadTimeout("token=private-token full report"))
    result = await channel.send_part("1", "part")
    assert result == {"success": False, "outcome": "uncertain", "error_code": "transport_error"}
    assert len(client.calls) == 1


@pytest.mark.parametrize("text", ["", "  ", "a" * 4097, "😀" * 2049, "\ud800"])
async def test_invalid_part_blocked_before_http_no_truncation(transport, text):
    _, channel, install = transport
    client = install(Response(200, {}))
    assert await channel.send_part("1", text) == {"success": False, "outcome": "blocked", "error_code": "invalid_part"}
    assert client.calls == []


async def test_disabled_transport_is_blocked_before_http(transport):
    _, channel, install = transport
    channel.token = ""
    client = install(Response(200, {}))
    assert (await channel.send_part("1", "part"))["outcome"] == "blocked"
    assert client.calls == []


@pytest.mark.parametrize("chat_id", [None, "", " 1 "])
async def test_invalid_destination_blocked(transport, chat_id):
    _, channel, install = transport
    client = install(Response(200, {}))
    assert (await channel.send_part(chat_id, "part"))["outcome"] == "blocked"
    assert client.calls == []


async def test_plain_mode_and_no_unsupported_format(transport):
    _, channel, install = transport
    client = install(Response(200, {}))
    await channel.send_part("1", "part", parse_mode=None)
    assert "format" not in client.calls[0][1]["json"]
    assert "parse_mode" not in client.calls[0][1]["json"]
    assert (await channel.send_part("1", "part", parse_mode="Markdown"))["outcome"] == "blocked"
    assert len(client.calls) == 1


async def test_telegram_unconfirmed_negative_not_retryable(monkeypatch):
    client = Client(Response(429, {"description": "unknown proxy response"}))
    monkeypatch.setattr(tg_module.httpx, "AsyncClient", lambda **kwargs: client)
    assert (await TelegramChannel(token="token").send_part("1", "part"))["outcome"] == "uncertain"
    assert len(client.calls) == 1


async def test_cancellation_is_not_false_success_or_known_rejection(transport):
    import asyncio

    _, channel, install = transport
    client = install(Response(200, {}))

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError

    client.post = cancelled
    with pytest.raises(asyncio.CancelledError):
        await channel.send_part("1", "part")
