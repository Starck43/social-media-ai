"""Fixed-authority, metadata-only Mail.ru WebDAV transport."""

import asyncio
from functools import partial
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

import httpx

from app.integrations.file_workspace.policy import FileOperation, ReadOnlyFolderPolicy, _components
from app.integrations.file_workspace.yandex_disk import DiskItem, DiskPage, MetadataError
from app.integrations.file_workspace.yandex_http import _encode_request, _request_once
from app.integrations.file_workspace.yandex_network import _connect_pinned, _resolve
from app.integrations.file_workspace.yandex_tls import _open_tls_to_ip

_HOST = "webdav.cloud.mail.ru"
_DAV = "{DAV:}"
_QUERY = b'<propfind xmlns="DAV:"><prop><resourcetype/></prop></propfind>'


async def open_mailru_tls():
    return await _connect_pinned(partial(_open_tls_to_ip, _server_hostname=_HOST), resolver=partial(_resolve, _HOST))


def credentials(account: str, password: str) -> tuple[str, str]:
    account = account.strip()
    if (
        len(account) > 254
        or account.count("@") != 1
        or not all(account.split("@"))
        or ":" in account
        or not 1 <= len(password) <= 512
        or any(not 33 <= ord(char) <= 126 for char in account + password)
    ):
        raise ValueError("invalid_credentials")
    return account, password


def _href(value: str) -> tuple[str, ...]:
    if not value or value.startswith("//") or any(ord(char) <= 32 for char in value) or "?" in value or "#" in value:
        raise ValueError
    url = urlsplit(value)
    if (url.scheme or url.netloc) and (url.scheme != "https" or url.netloc not in {_HOST, _HOST + ":443"}):
        raise ValueError
    if not url.path.startswith("/") or "//" in url.path:
        raise ValueError
    path = url.path.removesuffix("/") or "/"
    parts = tuple(unquote(part, errors="strict") for part in path[1:].split("/")) if path != "/" else ()
    if not path.startswith("/") or any("/" in part for part in parts):
        raise ValueError
    return _components("/" + "/".join(parts))


def _items(content: bytes, requested: tuple[str, ...], policy: ReadOnlyFolderPolicy) -> list[DiskItem]:
    try:
        xml = content.decode("utf-8")
        if "<!" in xml:
            raise ValueError
        document = ElementTree.fromstring(xml)
        if document.tag != _DAV + "multistatus":
            raise ValueError
        entries = []
        seen = set()
        for row in document:
            if row.tag != _DAV + "response":
                continue
            path = _href(row.findtext(_DAV + "href", default=""))
            if (path != requested and (path[:-1] != requested or len(path) != len(requested) + 1)) or path in seen:
                raise ValueError
            seen.add(path)
            kind = None
            for propstat in row.findall(_DAV + "propstat"):
                status = propstat.findtext(_DAV + "status", default="").split()
                resource = propstat.find(f"{_DAV}prop/{_DAV}resourcetype")
                if (
                    len(status) >= 2
                    and status[0] in {"HTTP/1.0", "HTTP/1.1"}
                    and status[1] == "200"
                    and resource is not None
                ):
                    kind = "dir" if resource.find(_DAV + "collection") is not None else "file"
                    break
            if path == requested:
                if kind != "dir":
                    raise ValueError
            elif kind is not None:
                canonical = "/" + "/".join(path)
                if policy.permits(canonical, FileOperation.LIST):
                    entries.append(DiskItem(canonical, path[-1], kind))
        if requested not in seen:
            raise ValueError
    except (ValueError, ElementTree.ParseError):
        raise MetadataError("invalid_provider_response") from None
    return entries


async def list_mailru_folder(
    *, account: str, password: str, folder: str, policy: ReadOnlyFolderPolicy, offset: int = 0, limit: int = 100
) -> DiskPage:
    if not policy.permits(folder, FileOperation.LIST):
        raise MetadataError("access_denied")
    if type(offset) is not int or not 0 <= offset <= 1_000_000 or type(limit) is not int or not 1 <= limit <= 100:
        raise MetadataError("invalid_page")
    try:
        account, password = credentials(account, password)
        requested = _components(folder)
        path = "/" + "/".join(quote(part, safe="") for part in requested)
        if requested:
            path += "/"
        request = httpx.Request(
            "PROPFIND",
            "https://" + _HOST + path,
            content=_QUERY,
            headers={
                "Depth": "1",
                "Content-Type": "application/xml; charset=utf-8",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        request = next(httpx.BasicAuth(account, password).auth_flow(request))
        protocol, wire = _encode_request(request)
        async with asyncio.timeout(15):
            response = await _request_once(protocol, wire, open_mailru_tls, 256 * 1024, success_status=207)
    except (ValueError, TimeoutError, httpx.HTTPError):
        raise MetadataError("provider_unavailable") from None
    if response.status_code in {401, 403}:
        raise MetadataError("reauthorization_required")
    if response.status_code == 429:
        raise MetadataError("rate_limited")
    if response.status_code != 207:
        raise MetadataError("provider_unavailable")
    entries = sorted(_items(response._body, requested, policy), key=lambda item: item.name)
    end = offset + limit
    return DiskPage(tuple(entries[offset:end]), end if end < len(entries) else None)
