"""Minimal staged media snapshots; no downloads, secrets or provider bodies.

None means absent/malformed/legacy-unknown; [] means explicitly no attachments.
Only type + a conservative HTTPS reference survive. Rejected references keep
an image/video placeholder with url=None, so missing media is not called empty.
This is syntax validation, not DNS/reachability or provider-media acceptance.
"""
from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

_MEDIA_TYPES = {"photo": "image", "image": "image", "video": "video", "video_file": "video"}


def _staged_media_url(value: Any) -> str | None:
    if type(value) is not str or not value or len(value) > 2048 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname.rstrip(".") if parsed.hostname else None
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.port not in (None, 443)):
            return None
        if host == "api.telegram.org" or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # A DNS name is not proof of a publicly reachable origin; never
            # resolve/download it here. Reject bare local-style host names.
            if "." not in host:
                return None
        else:
            if not address.is_global:
                return None
    except ValueError:
        return None
    return value


def normalize_attachments(value: Any) -> list[dict[str, str | None]] | None:
    """Copy an allowlisted snapshot without conflating unknown with no media."""
    if type(value) is not list:
        return None
    result = []
    for entry in value:
        media_type = entry.get("type") if type(entry) is dict else None
        media_type = _MEDIA_TYPES.get(media_type.lower(), "unknown") if type(media_type) is str else "unknown"
        url = _staged_media_url(entry.get("url")) if type(entry) is dict and media_type != "unknown" else None
        result.append({"type": media_type, "url": url})
    return result
