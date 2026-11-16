"""Bounded private text download using two fixed provider hosts; no revision claim.

Application-only client, never an arbitrary URL proxy. No redirects/retries,
compression, environment proxies, public links, logs or persisted source bytes.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import h11

from app.integrations.file_workspace.plain_text import MAX_INPUT_BYTES, extract_plain_text
from app.integrations.file_workspace.policy import _components
from app.integrations.file_workspace.yandex_download_target import parse_download_target
from app.integrations.file_workspace.yandex_network import _validated_addresses
from app.integrations.file_workspace.yandex_tls import _tls_context

API_HOST = "cloud-api.yandex.net"
DOWNLOAD_HOST = "downloader.dst.yandex.ru"
CONTENT_TIMEOUT = 10.0
MAX_HEADER_BYTES = 16384
MAX_METADATA_BYTES = 16384
_FILE_FIELDS = "path,name,type,size,md5,modified"


class FileDownloadUnavailable(Exception):
    def __init__(self):
        super().__init__("file_download_unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class DownloadedText:
    payload: bytes
    # No immutable exact-byte provider revision semantics established here.
    provider_version: None = None


async def open_file_tls(host):
    if host not in (API_HOST, DOWNLOAD_HOST):
        raise FileDownloadUnavailable()
    loop = asyncio.get_running_loop()
    answers = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    address = ipaddress.ip_address(_validated_addresses(answers)[0])
    context = _tls_context()
    sock = socket.socket(socket.AF_INET if address.version == 4 else socket.AF_INET6, socket.SOCK_STREAM)
    try:
        sock.setblocking(False)
        await loop.sock_connect(sock, (str(address), 443))
        if "SSLKEYLOGFILE" in os.environ:
            raise ValueError
        return await asyncio.open_connection(sock=sock, ssl=context, server_hostname=host, ssl_handshake_timeout=5.0)
    except BaseException:
        sock.close()
        raise


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _json(body):
    if type(body) is not bytes or len(body) > MAX_METADATA_BYTES:
        raise ValueError
    return json.loads(body, object_pairs_hook=_unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _file_metadata(body, path, name):
    value = _json(body)
    if (
        type(value) is not dict
        or set(value) != {"path", "name", "type", "size", "md5", "modified"}
        or value["path"] != path
        or value["name"] != name
        or value["type"] != "file"
        or type(value["size"]) is not int
        or not 0 <= value["size"] <= MAX_INPUT_BYTES
        or type(value["md5"]) is not str
        or len(value["md5"]) != 32
        or any(c not in "0123456789abcdef" for c in value["md5"])
        or type(value["modified"]) is not str
        or not 1 <= len(value["modified"]) <= 64
        or not value["modified"].isascii()
        or any(ord(c) < 32 or ord(c) > 126 for c in value["modified"])
    ):
        raise ValueError
    return value


async def _exchange(reader, writer, protocol, request, maximum):
    writer.write(request)
    await writer.drain()
    body = bytearray()
    status = None
    wire = chunks = 0
    while True:
        event = protocol.next_event()
        if event is h11.NEED_DATA:
            chunk = await reader.read(8192)
            if type(chunk) is not bytes or len(chunk) > 8192:
                raise ValueError
            chunks += 1
            wire += len(chunk)
            if chunks > 1024 or wire > maximum * 2 + MAX_HEADER_BYTES * 2:
                raise ValueError
            protocol.receive_data(chunk)
        elif isinstance(event, h11.Response):
            if status is not None or event.status_code != 200 or event.http_version != b"1.1":
                raise ValueError
            status = event.status_code
            if sum(len(k) + len(v) + 4 for k, v in event.headers) > MAX_HEADER_BYTES:
                raise ValueError
            encodings = [v.strip().lower() for k, v in event.headers if k.lower() == b"content-encoding"]
            if len(encodings) > 1 or any(v != b"identity" for v in encodings):
                raise ValueError
            if any(k.lower() == b"content-range" for k, _ in event.headers):
                raise ValueError
            for key, value in event.headers:
                if key.lower() == b"content-length" and int(value) > maximum:
                    raise ValueError
        elif isinstance(event, h11.Data):
            if status is None or len(body) + len(event.data) > maximum:
                raise ValueError
            body.extend(event.data)
        elif isinstance(event, h11.EndOfMessage):
            if status != 200 or event.headers:
                raise ValueError
            return bytes(body)
        else:
            raise ValueError


class YandexTextClient:
    def __init__(self, *, _opener=open_file_tls):
        self._opener = _opener

    async def _get(self, host, target, token, maximum):
        # host and origin-form target are constructed internally, never request URLs.
        if host not in (API_HOST, DOWNLOAD_HOST) or not target.startswith("/") or not target.isascii():
            raise ValueError
        protocol = h11.Connection(h11.CLIENT, max_incomplete_event_size=MAX_HEADER_BYTES)
        request = protocol.send(
            h11.Request(
                method=b"GET",
                target=target.encode("ascii"),
                headers=[
                    (b"Host", host.encode()),
                    (b"Authorization", b"OAuth " + token.encode("ascii")),
                    (b"Accept", b"*/*"),
                    (b"Accept-Encoding", b"identity"),
                    (b"Connection", b"close"),
                ],
            )
        ) + protocol.send(h11.EndOfMessage())
        if len(request) > 16384:
            raise ValueError
        writer = None
        try:
            reader, writer = await self._opener(host)
            return await _exchange(reader, writer, protocol, request, maximum)
        finally:
            if writer is not None:
                writer.close()
                try:
                    async with asyncio.timeout(1):
                        await writer.wait_closed()
                except (TimeoutError, OSError):
                    pass

    async def download(self, *, path, name, token):
        """Read exact bounded .txt representation; equality/checksum is NOT a revision.

        Four pinned exchanges under one deadline: current metadata, private link,
        body, metadata again. Unknown download hosts or redirects fail closed.
        MD5/size/pre-post equality detect some failures, not snapshot/identity/ABA.
        """
        try:
            if type(path) is not str or path != "disk:/" + "/".join(_components(path)) or len(path.encode()) > 2048:
                raise ValueError
            extract_plain_text(b"", name=name)
            if type(token) is not str or not 1 <= len(token) <= 2048 or any(not 33 <= ord(c) <= 126 for c in token):
                raise ValueError
            if path.rsplit("/", 1)[-1] != name:
                raise ValueError
            async with asyncio.timeout(CONTENT_TIMEOUT):
                resource = "/v1/disk/resources?" + urlencode({"path": path, "fields": _FILE_FIELDS})
                before = _file_metadata(await self._get(API_HOST, resource, token, MAX_METADATA_BYTES), path, name)
                link = "/v1/disk/resources/download?" + urlencode({"path": path, "fields": "href,method,templated"})
                target = parse_download_target(await self._get(API_HOST, link, token, MAX_METADATA_BYTES))
                url = urlsplit(target._url)
                origin_form = url.path + ("?" + url.query if url.query else "")
                payload = await self._get(DOWNLOAD_HOST, origin_form, token, MAX_INPUT_BYTES)
                after = _file_metadata(await self._get(API_HOST, resource, token, MAX_METADATA_BYTES), path, name)
                if (
                    before != after
                    or len(payload) != before["size"]
                    or hashlib.md5(payload, usedforsecurity=False).hexdigest() != before["md5"]
                ):
                    raise ValueError
                return DownloadedText(payload)
        except Exception:
            pass
        raise FileDownloadUnavailable()
