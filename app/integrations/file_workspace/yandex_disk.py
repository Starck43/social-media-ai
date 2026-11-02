"""Bounded metadata-only Yandex Disk reader; not connected to web or jobs.

The caller must authorize the current actor, tenant, owner/share, connection and
selected root for each call. A folder policy alone does not establish those rights.
"""

from __future__ import annotations

import json
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.integrations.file_workspace.policy import FileOperation, ReadOnlyFolderPolicy, _components


class MetadataClient(Protocol):
    """The narrow streaming interface required by the metadata reader."""

    def stream(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str],
        timeout: httpx.Timeout,
        follow_redirects: bool,
    ) -> AbstractAsyncContextManager[Any]: ...


_METADATA_URL = "https://cloud-api.yandex.net/v1/disk/resources"
_MAX_RESPONSE_BYTES = 256 * 1024
_MAX_PAGE_SIZE = 100
_MAX_OFFSET = 1_000_000
_FIELDS = "path,type,_embedded.items.path,_embedded.items.name,_embedded.items.type,_embedded.offset"


class MetadataError(Exception):
    """Sanitized failure code: never includes token, provider URL or paths."""


@dataclass(frozen=True)
class DiskItem:
    path: str
    name: str
    kind: str


@dataclass(frozen=True)
class DiskPage:
    items: tuple[DiskItem, ...]
    # A continuation hint, not a provider snapshot or proof of complete scan.
    next_offset: int | None
    truncated: bool = False
    # Populated only by the owner-bound service after its policy check.
    current_path: str | None = None
    root_path: str | None = None


def _disk_path(parts: tuple[str, ...]) -> str:
    return "disk:/" + "/".join(parts)


async def list_folder(
    client: MetadataClient,
    *,
    token: str,
    folder: str,
    policy: ReadOnlyFolderPolicy,
    offset: int = 0,
    limit: int = 100,
) -> DiskPage:
    """Read at most one folder metadata page; never download or mutate files.

    The injected client is an application-controlled dependency, not user input.
    Continuation is internal: provider offset paging is not a stable snapshot.
    """
    if not isinstance(policy, ReadOnlyFolderPolicy) or not policy.permits(folder, FileOperation.LIST):
        raise MetadataError("access_denied")
    if type(offset) is not int or not 0 <= offset <= _MAX_OFFSET:
        raise MetadataError("invalid_page")
    if type(limit) is not int or not 1 <= limit <= _MAX_PAGE_SIZE:
        raise MetadataError("invalid_page")
    if not isinstance(token, str) or not 1 <= len(token) <= 2048 or any(not 33 <= ord(c) <= 126 for c in token):
        raise MetadataError("invalid_credentials")

    requested = _components(folder)
    try:
        async with client.stream(
            "GET",
            _METADATA_URL,
            params={"path": _disk_path(requested), "limit": limit, "offset": offset, "sort": "name", "fields": _FIELDS},
            headers={"Authorization": f"OAuth {token}", "Accept": "application/json"},
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
        ) as response:
            if response.status_code in (401, 403):
                raise MetadataError("reauthorization_required")
            if response.status_code == 429:
                raise MetadataError("rate_limited")
            if response.status_code != 200:
                raise MetadataError("provider_unavailable")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                    raise MetadataError("response_too_large")
                body.extend(chunk)
    except httpx.HTTPError:
        raise MetadataError("provider_unavailable") from None
    try:
        data = json.loads(body)
        if not isinstance(data, dict) or data.get("type") != "dir" or _components(data.get("path")) != requested:
            raise ValueError
        embedded = data.get("_embedded", {"items": [], "offset": offset})
        if not isinstance(embedded, dict) or type(embedded.get("offset")) is not int or embedded["offset"] != offset:
            raise ValueError
        items = embedded.get("items")
        if not isinstance(items, list) or len(items) > limit:
            raise ValueError
        result = []
        seen = set()
        for item in items:
            if not isinstance(item, dict) or item.get("type") not in ("file", "dir"):
                raise ValueError
            path = _components(item.get("path"))
            if len(path) != len(requested) + 1 or path[:-1] != requested or item.get("name") != path[-1]:
                raise ValueError
            if path in seen:
                raise ValueError
            seen.add(path)
            canonical = _disk_path(path)
            if policy.permits(canonical, FileOperation.LIST):
                result.append(DiskItem(canonical, item["name"], item["type"]))
    except (ValueError, TypeError, KeyError, RecursionError):
        raise MetadataError("invalid_provider_response") from None
    full = len(items) == limit
    truncated = full and offset + len(items) > _MAX_OFFSET
    next_offset = offset + len(items) if full and not truncated else None
    return DiskPage(tuple(result), next_offset, truncated)
