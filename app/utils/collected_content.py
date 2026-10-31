"""Canonical raw staging rows shared by collection and push admission."""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

_TG_HANDLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,63}$")


def _telegram_public_handle(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    if "t.me/" in candidate:
        candidate = candidate.split("t.me/", 1)[1].strip("/").split("/", 1)[0]
    candidate = candidate.lstrip("@").strip("/")
    if candidate.lower() in {"c", "joinchat", "s", "addstickers", "proxy", "iv"}:
        return None
    return candidate if _TG_HANDLE_RE.fullmatch(candidate) else None


def _sanitize_staged_permalink(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 2048:
        return None
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in '<>"\\' for c in cleaned):
        return None
    if cleaned.startswith("//"):
        return None
    if ":" not in cleaned:
        return cleaned
    try:
        parsed = urlsplit(cleaned)
        host = parsed.hostname.rstrip(".").lower() if parsed.hostname else None
        if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
            return None
        parsed.port
        if host == "api.telegram.org" or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host:
                return None
        else:
            if not address.is_global:
                return None
    except ValueError:
        return None
    return cleaned


def _build_permalink(source: Any, external_id: str | int | None) -> str | None:
    """A public link to one item, or None when the platform has no known shape.

    Kept deliberately dumb: a wrong link is worse than no link, so anything not
    recognised returns None and the UI simply shows the text without a link.
    """
    if external_id is None:
        return None
    eid = str(external_id).strip()
    if not eid:
        return None
    params = getattr(source, "params", None) or {}
    platform_code = (params.get("platform") or getattr(getattr(source, "platform", None), "code", "") or "").lower()
    try:
        if "vk" in platform_code:
            # VK post ids arrive as "{owner_id}_{post_id}" or bare "{post_id}".
            if "_" in eid:
                return _sanitize_staged_permalink(f"https://vk.com/wall{eid}")
            owner = params.get("owner_id") or params.get("group_id") or ""
            built = f"https://vk.com/wall{owner}_{eid}" if owner else f"https://vk.com/wall{eid}"
            return _sanitize_staged_permalink(built)
        if params.get("mode") == "push" or "telegram" in platform_code:
            handle = (
                _telegram_public_handle(params.get("username"))
                or _telegram_public_handle(params.get("channel"))
                or _telegram_public_handle(getattr(source, "external_id", None))
            )
            msg_id: str | None = None
            if "_" in eid:
                prefix, suffix = eid.rsplit("_", 1)
                if suffix.isdigit() and int(suffix) > 0:
                    msg_id = suffix
                    if not handle:
                        handle = _telegram_public_handle(prefix)
            elif eid.isdigit() and int(eid) > 0 and handle:
                msg_id = eid
            if handle and msg_id:
                return f"https://t.me/{handle}/{msg_id}"
            return f"https://t.me/{eid}" if eid.lstrip("-").isdigit() else None
    except Exception:  # noqa: BLE001 — a link is a nicety, not a reason to fail
        return None
    return None


def build_staged_rows(
    content: list[dict],
    source: Any,
    run_id: int | None,
    selection: dict[str, tuple[str, str | None]] | None = None,
) -> list[dict]:
    from app.services.ai.dedup import item_hash
    from app.utils.content_attachments import normalize_attachments
    from app.utils.date_parsing import universal_date_parser

    hashes = [item_hash(item) for item in content]
    rows: list[dict] = []
    for item, h in zip(content, hashes):
        status, selection_reason = selection.get(h, ("selected", None)) if selection else ("selected", None)
        # Platforms hand the publication date over in several shapes (datetime,
        # unix seconds, "2026-10-02T10:00:00Z"); normalise once here so the
        # column, the ordering and the UI all agree on what a date is.
        raw_published = item.get("published_at") or item.get("date") or item.get("created_at")
        published = universal_date_parser(raw_published) if raw_published is not None else None
        external_id = item.get("external_id") or item.get("id")
        permalink = _sanitize_staged_permalink(item.get("permalink") or item.get("url"))
        if not permalink and external_id:
            permalink = _build_permalink(source, external_id)
        if permalink:
            item["permalink"] = permalink  # Same saved URL reaches immediate analysis.
        elif "permalink" in item and item.get("permalink") is not None:
            item["permalink"] = None
        rows.append(
            {
                "run_id": run_id,
                "source_id": source.id,
                "external_id": str(external_id) if external_id is not None else None,
                "content_hash": h,
                "platform": item.get("platform"),
                "content_type": item.get("content_type") or "post",
                "parent_external_id": item.get("parent_external_id"),
                "lane_id": item.get("lane_id"),
                "selection_status": status,
                "selection_reason": selection_reason,
                "published_at": published,
                "media_type": item.get("media_type") or item.get("type"),
                "attachments": normalize_attachments(item.get("attachments")),
                "text": item.get("text"),
                "metrics": {
                    **(item.get("metrics") if isinstance(item.get("metrics"), dict) else {}),
                    **(
                        {"parent_context": item["parent_context"]}
                        if isinstance(item.get("parent_context"), dict)
                        else {}
                    ),
                    **{
                        key: item[key]
                        for key in ("reactions", "comments", "views", "metric_availability")
                        if key in item
                    },
                },
                "author": (
                    item.get("author")
                    if isinstance(item.get("author"), dict)
                    else (
                        {
                            "id": next(
                                item[k]
                                for k in ("from_id", "owner_id", "author_id", "user_id")
                                if item.get(k) is not None
                            )
                        }
                        if any(item.get(k) is not None for k in ("from_id", "owner_id", "author_id", "user_id"))
                        else None
                    )
                ),
                "permalink": permalink,
            }
        )
    return rows
