"""Read-only Bitrix24 task list adapter.

The provider remains authoritative. This module validates the small projection
needed by the workspace and never calls a mutating Bitrix24 method.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import date, datetime
from typing import Any

from app.integrations.bitrix24.client import (
    Bitrix24AccessDeniedError,
    Bitrix24AuthError,
    Bitrix24Error,
    Bitrix24RateLimitError,
    bitrix24_service,
)

PAGE_SIZE = 50
FIELDS = ("ID", "TITLE", "STATUS", "DEADLINE", "RESPONSIBLE_ID", "AUDITORS")


class BitrixTaskSourceError(RuntimeError):
    """A malformed, partial, or unavailable task response."""

    def __init__(self, code: str = "source_unavailable") -> None:
        self.code = code
        super().__init__(code)


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if type(value) is int:
        result = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        result = int(value)
    else:
        return None
    return result if result > 0 else None


def _task_list(response: Any, *, offset: int) -> tuple[list[dict[str, Any]], int | None]:
    if not isinstance(response, dict) or response.get("error"):
        raise BitrixTaskSourceError("source_unavailable")
    rows = response.get("result")
    # Bitrix24's legacy tasks.task.list returns an array in `result`; retain
    # compatibility with the documented object envelope used by some portals.
    if isinstance(rows, dict):
        rows = rows.get("tasks")
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise BitrixTaskSourceError("source_unavailable")
    task_ids = [_integer(row.get("id", row.get("ID"))) for row in rows if isinstance(row, dict)]
    if (
        len(task_ids) != len(rows)
        or any(task_id is None for task_id in task_ids)
        or len(set(task_ids)) != len(task_ids)
    ):
        raise BitrixTaskSourceError("source_unavailable")
    next_offset = response.get("next")
    if next_offset is not None:
        next_offset = _integer(next_offset)
        if next_offset != offset + PAGE_SIZE or len(rows) != PAGE_SIZE:
            raise BitrixTaskSourceError("source_unavailable")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise BitrixTaskSourceError("source_unavailable")
        task_id = _integer(row.get("id", row.get("ID")))
        title = row.get("title", row.get("TITLE"))
        status = _integer(row.get("status", row.get("STATUS")))
        deadline = row.get("deadline", row.get("DEADLINE"))
        responsible = _integer(row.get("responsibleId", row.get("RESPONSIBLE_ID")))
        if task_id is None or not isinstance(title, str) or not title.strip() or status is None or responsible is None:
            raise BitrixTaskSourceError("source_unavailable")
        if deadline == "":
            deadline = None
        if deadline is not None and not isinstance(deadline, str):
            raise BitrixTaskSourceError("source_unavailable")
        if deadline is not None:
            try:
                datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            except ValueError:
                try:
                    date.fromisoformat(deadline)
                except ValueError:
                    raise BitrixTaskSourceError("source_unavailable") from None
        auditors = row.get("auditors", row.get("AUDITORS", [])) or []
        if not isinstance(auditors, list) or any(_integer(value) is None for value in auditors):
            raise BitrixTaskSourceError("source_unavailable")
        result.append(
            {
                "external_id": str(task_id),
                "title": title.strip()[:500],
                "status": status,
                "due_at": deadline,
                "responsible_id": responsible,
                "auditors": [int(value) for value in auditors],
            }
        )
    return result, next_offset


async def current_user_id(*, tenant_id: int, actor_id: int, connection_id: int, session) -> int:
    try:
        response = await bitrix24_service.call_method(
            tenant_id=tenant_id,
            actor_id=actor_id,
            connection_id=connection_id,
            method_name="user.current",
            session=session,
        )
    except Bitrix24AccessDeniedError as exc:
        # user.current is not proof of the task REST scope; only task-list
        # denial below is surfaced as a missing task permission.
        raise BitrixTaskSourceError("source_unavailable") from exc
    except Bitrix24AuthError as exc:
        raise BitrixTaskSourceError("reauth_required") from exc
    except Bitrix24RateLimitError as exc:
        raise BitrixTaskSourceError("rate_limited") from exc
    except Bitrix24Error as exc:
        raise BitrixTaskSourceError("source_unavailable") from exc
    payload = response.get("result") if isinstance(response, dict) else None
    profile_id = _integer(payload.get("ID", payload.get("id"))) if isinstance(payload, dict) else None
    if profile_id is None:
        raise BitrixTaskSourceError("source_unavailable")
    return profile_id


async def _stream(
    *, tenant_id: int, actor_id: int, connection_id: int, profile_id: int, kind: str, offset: int, session
):
    field = "RESPONSIBLE_ID" if kind == "assigned" else "AUDITOR"
    params = {
        f"filter[{field}]": str(profile_id),
        "order[ID]": "desc",
        "start": str(offset),
    }
    params.update({f"select[{index}]": field_name for index, field_name in enumerate(FIELDS)})
    try:
        response = await bitrix24_service.call_method(
            tenant_id=tenant_id,
            actor_id=actor_id,
            connection_id=connection_id,
            method_name="tasks.task.list",
            params=params,
            session=session,
        )
    except Bitrix24AccessDeniedError as exc:
        raise BitrixTaskSourceError("task_scope_required") from exc
    except Bitrix24AuthError as exc:
        raise BitrixTaskSourceError("reauth_required") from exc
    except Bitrix24RateLimitError as exc:
        raise BitrixTaskSourceError("rate_limited") from exc
    except Bitrix24Error as exc:
        raise BitrixTaskSourceError("source_unavailable") from exc
    return _task_list(response, offset=offset)


def task_url(portal_domain: str, profile_id: int, task_id: int) -> str:
    """Build a portal-local link after REST transport has validated the host."""
    from urllib.parse import urlsplit

    candidate = portal_domain.strip().lower()
    parts = urlsplit(f"https://{candidate}")
    if (
        not candidate
        or parts.scheme != "https"
        or parts.hostname != candidate
        or parts.path
        or parts.query
        or parts.fragment
        or parts.port is not None
        or "@" in candidate
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", candidate)
    ):
        raise BitrixTaskSourceError("source_unavailable")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise BitrixTaskSourceError("source_unavailable")
    host = candidate
    return f"https://{host}/company/personal/user/{profile_id}/tasks/task/view/{task_id}/"


def normalized_deadline(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            raise BitrixTaskSourceError("source_unavailable") from None
    return parsed.isoformat()
