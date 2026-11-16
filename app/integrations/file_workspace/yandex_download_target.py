"""Validate a Yandex download-link response without opening or exposing it.

This is an unconnected foundation, not a downloader or authorization check.
The returned URL is sensitive even if it contains no OAuth token. A future
consumer must reauthorize the actor and file, apply network/DNS/TLS and byte
limits to each hop, never log or redirect the URL to a browser, and securely
handle the provider token. Only the documented example host is admitted here;
other legitimate hosts are denied until reviewed with provider evidence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, unquote, urlsplit

_MAX_RESPONSE = 16 * 1024
_MAX_URL = 12 * 1024
_HOST = "downloader.dst.yandex.ru"
_SENSITIVE_QUERY_NAMES = frozenset({"access_token", "oauth_token", "authorization", "token"})


class DownloadTargetError(Exception):
    """Generic failure with no provider response, path, token or URL."""


@dataclass(frozen=True)
class DownloadTarget:
    # Private by convention; never include the bearer-like URL in repr/logs.
    _url: str = field(repr=False)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _validated_url(url: object) -> str:
    if not isinstance(url, str) or not 1 <= len(url) <= _MAX_URL or not url.isascii():
        raise ValueError
    if any(ord(char) < 33 or ord(char) == 127 for char in url) or "\\" in url or "#" in url:
        raise ValueError
    if re.search(r"%(?![0-9a-fA-F]{2})", url):
        raise ValueError
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc not in (_HOST, f"{_HOST}:443"):
        raise ValueError
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise ValueError
    if not parts.path.startswith("/disk/") or len(parts.path) <= len("/disk/"):
        raise ValueError
    # Decode once only for checks. Encoded separators, traversal, controls,
    # double-encoded percent, and ambiguous backslashes are not accepted.
    for segment in parts.path.split("/"):
        decoded = unquote(segment, encoding="utf-8", errors="strict")
        if decoded in (".", "..") or any(char in decoded for char in "/\\%"):
            raise ValueError
        if any(ord(char) < 33 or ord(char) == 127 for char in decoded):
            raise ValueError
    query = parse_qsl(parts.query, keep_blank_values=True, max_num_fields=64, errors="strict")
    if any(
        key.lower() in _SENSITIVE_QUERY_NAMES
        or not key.isascii()
        or "%" in key
        or "\\" in key
        or any(ord(char) < 32 or ord(char) == 127 for char in key + value)
        for key, value in query
    ):
        raise ValueError
    return url


def parse_download_target(body: bytes) -> DownloadTarget:
    """Parse one bounded provider Link response, keeping its URL private.

    This does not request the link, follow redirects or read file data. It must
    only be called after fresh authorization, on a response from the reviewed
    fixed metadata API, not directly on a user-supplied URL.
    """
    try:
        if not isinstance(body, bytes) or len(body) > _MAX_RESPONSE:
            raise ValueError
        data = json.loads(body, object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or set(data) != {"href", "method", "templated"}:
            raise ValueError
        if data["method"] != "GET" or data["templated"] is not False:
            raise ValueError
        return DownloadTarget(_validated_url(data["href"]))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise DownloadTargetError("download_unavailable") from None
