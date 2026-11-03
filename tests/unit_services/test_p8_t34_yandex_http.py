"""HTTP transport tests with fake streams; no sockets or external provider."""

import asyncio
import json
import logging

import httpx
import pytest

from app.integrations.file_workspace import yandex_disk, yandex_http
from app.integrations.file_workspace.policy import ReadOnlyFolderPolicy
from app.integrations.file_workspace.yandex_disk import _FIELDS, _METADATA_URL, MetadataError, list_folder

__source_only__ = True


class FakeReader:
    def __init__(self, data, chunk_size=8192):
        self.data = data
        self.chunk_size = chunk_size
        self.read_calls = 0

    async def read(self, count):
        self.read_calls += 1
        size = min(count, self.chunk_size)
        data, self.data = self.data[:size], self.data[size:]
        return data


class FakeWriter:
    def __init__(self):
        self.written = b""
        self.closed = False

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


def make_client(data, *, chunk_size=8192):
    reader, writer = FakeReader(data, chunk_size), FakeWriter()
    calls = []

    async def opener():
        calls.append(True)
        return reader, writer

    return yandex_http.YandexMetadataClient(_opener=opener), reader, writer, calls


def http_response(body, status=b"200 OK", headers=b""):
    return (
        b"HTTP/1.1 " + status + b"\r\n" + headers + b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )


def folder_body():
    return json.dumps(
        {
            "path": "disk:/private folder",
            "type": "dir",
            "_embedded": {
                "offset": 0,
                "items": [{"path": "disk:/private folder/report.txt", "name": "report.txt", "type": "file"}],
            },
        }
    ).encode()


@pytest.mark.asyncio
async def test_metadata_reader_uses_one_fixed_get_without_sensitive_logs(caplog, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:1234")
    caplog.set_level(logging.DEBUG)
    client, reader, writer, calls = make_client(http_response(folder_body()), chunk_size=7)
    page = await list_folder(
        client, token="fake-secret", folder="/private folder", policy=ReadOnlyFolderPolicy("/private folder")
    )
    assert [item.name for item in page.items] == ["report.txt"]
    assert calls == [True] and writer.closed and reader.read_calls > 1
    assert writer.written.startswith(b"GET /v1/disk/resources?")
    assert b"Host: cloud-api.yandex.net\r\n" in writer.written
    assert b"Authorization: OAuth fake-secret\r\n" in writer.written
    assert b"Accept-Encoding: identity\r\n" in writer.written
    assert b"Connection: close\r\n" in writer.written
    assert b"path=disk%3A%2Fprivate+folder" in writer.written
    assert "fake-secret" not in caplog.text and "private folder" not in caplog.text


@pytest.mark.asyncio
async def test_non_200_body_is_drained_not_returned():
    """A refusal with a body must reach EndOfMessage, not be abandoned mid-message."""
    data = b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 32\r\n\r\n" + b"x" * 32
    client, _, writer, _ = make_client(data)
    with pytest.raises(MetadataError, match="^rate_limited$"):
        await list_folder(client, token="fake-secret", folder="/x", policy=ReadOnlyFolderPolicy("/x"))
    assert writer.closed


@pytest.mark.asyncio
async def test_unframed_body_ends_at_connection_close():
    """RFC 9112 §3.3.2: no Content-Length and no Transfer-Encoding ends at EOF."""
    client, _, writer, _ = make_client(b"HTTP/1.1 200 OK\r\n\r\n" + folder_body())
    page = await list_folder(
        client, token="fake-secret", folder="/private folder", policy=ReadOnlyFolderPolicy("/private folder")
    )
    assert page.items[0].name == "report.txt" and writer.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,code",
    [
        (http_response(b"sensitive", b"302 Found", b"Location: https://attacker.invalid\r\n"), "provider_unavailable"),
        (http_response(b"sensitive", b"401 Unauthorized"), "reauthorization_required"),
        (http_response(b"sensitive", b"403 Forbidden"), "reauthorization_required"),
        (http_response(b"sensitive", b"429 Too Many Requests"), "rate_limited"),
    ],
)
async def test_non_200_status_is_not_followed_or_read_as_data(body, code):
    client, _, writer, calls = make_client(body)
    with pytest.raises(MetadataError, match=f"^{code}$"):
        await list_folder(client, token="fake-secret", folder="/x", policy=ReadOnlyFolderPolicy("/x"))
    assert calls == [True] and writer.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data,code",
    [
        (b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: 0\r\n\r\n", "provider_unavailable"),
        (b"HTTP/1.1 200 OK\r\nContent-Length: 262145\r\n\r\n", "response_too_large"),
        (b"HTTP/1.1 200 OK\r\nX-Big: " + b"a" * 17000 + b"\r\n\r\n", "provider_unavailable"),
        (b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nshort", "provider_unavailable"),
        (b"HTTP/1.1 100 Continue\r\n\r\n", "provider_unavailable"),
        (b"not http\r\n\r\n", "provider_unavailable"),
    ],
)
async def test_malformed_unbounded_or_encoded_response_fails_closed(data, code):
    client, _, writer, calls = make_client(data)
    with pytest.raises(MetadataError, match=f"^{code}$"):
        await list_folder(client, token="fake-secret", folder="/x", policy=ReadOnlyFolderPolicy("/x"))
    assert calls == [True] and writer.closed


@pytest.mark.asyncio
async def test_oversized_chunked_body_is_bounded():
    chunk = b"x" * (256 * 1024 + 1)
    data = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        + hex(len(chunk))[2:].encode()
        + b"\r\n"
        + chunk
        + b"\r\n0\r\n\r\n"
    )
    client, _, writer, _ = make_client(data)
    with pytest.raises(MetadataError, match="^response_too_large$"):
        await list_folder(client, token="fake-secret", folder="/x", policy=ReadOnlyFolderPolicy("/x"))
    assert writer.closed


@pytest.mark.asyncio
async def test_valid_chunked_response_is_supported():
    body = folder_body()
    data = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        + hex(len(body))[2:].encode()
        + b"\r\n"
        + body
        + b"\r\n0\r\n\r\n"
    )
    client, _, writer, _ = make_client(data, chunk_size=9)
    page = await list_folder(
        client, token="fake-secret", folder="/private folder", policy=ReadOnlyFolderPolicy("/private folder")
    )
    assert page.items[0].name == "report.txt" and writer.closed


def base_args():
    return {
        "method": "GET",
        "url": _METADATA_URL,
        "params": {"path": "disk:/x", "limit": 10, "offset": 0, "sort": "name", "fields": _FIELDS},
        "headers": {"Authorization": "OAuth fake-secret", "Accept": "application/json"},
        "timeout": httpx.Timeout(10),
        "follow_redirects": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"method": "POST"},
        {"url": "https://attacker.invalid/"},
        {"follow_redirects": True},
        {"headers": {"Authorization": "OAuth fake\r\ninjected", "Accept": "application/json"}},
        {"headers": {"Authorization": "OAuth fake", "Accept": "application/json", "Host": "attacker.invalid"}},
        {"params": {"path": "disk:/x", "limit": 1, "offset": 0, "sort": "name", "fields": _FIELDS, "other": "x"}},
    ],
)
async def test_invalid_requests_are_rejected_before_connection(change):
    client, _, writer, calls = make_client(b"")
    args = base_args()
    args.update(change)
    with pytest.raises(httpx.HTTPError, match="^provider_unavailable$"):
        async with client.stream(**args):
            pass
    assert not calls and not writer.closed


@pytest.mark.asyncio
async def test_timeout_and_cancellation_close_stream(monkeypatch):
    client, reader, writer, calls = make_client(b"")

    async def slow_read(size):
        await asyncio.sleep(10)

    reader.read = slow_read
    # The caller's deadline is the single budget: the client no longer keeps a
    # second timer that the reader could not control.
    monkeypatch.setattr(yandex_disk, "_TOTAL_TIMEOUT", 0.01)
    with pytest.raises(MetadataError, match="^provider_unavailable$"):
        await list_folder(client, token="fake-secret", folder="/x", policy=ReadOnlyFolderPolicy("/x"))
    assert calls == [True] and writer.closed

    client, reader, writer, calls = make_client(b"")
    reader.read = slow_read
    task = asyncio.create_task(list_folder(client, token="fake-secret", folder="/x", policy=ReadOnlyFolderPolicy("/x")))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == [True] and writer.closed


@pytest.mark.asyncio
async def test_policy_denial_and_oversized_request_never_open_connection():
    client, _, _, calls = make_client(b"")
    with pytest.raises(MetadataError, match="^access_denied$"):
        await list_folder(client, token="fake-secret", folder="/outside", policy=ReadOnlyFolderPolicy("/allowed"))
    assert not calls
    args = base_args()
    args["params"]["path"] = "disk:/" + "a" * 17000
    with pytest.raises(httpx.HTTPError, match="^provider_unavailable$"):
        async with client.stream(**args):
            pass
    assert not calls


@pytest.mark.asyncio
async def test_io_error_is_sanitized_and_socket_is_closed(caplog):
    caplog.set_level(logging.DEBUG)
    client, reader, writer, calls = make_client(b"")

    async def bad_read(size):
        raise OSError("fake-secret /private folder")

    reader.read = bad_read
    with pytest.raises(MetadataError, match="^provider_unavailable$") as exc:
        await list_folder(
            client, token="fake-secret", folder="/private folder", policy=ReadOnlyFolderPolicy("/private folder")
        )
    assert str(exc.value) == "provider_unavailable"
    assert "fake-secret" not in caplog.text and "/private folder" not in caplog.text
    assert calls == [True] and writer.closed
