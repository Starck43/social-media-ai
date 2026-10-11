"""Shared Telegram raw admission; never call an analyzer or sender here."""
from __future__ import annotations
from datetime import datetime, timezone

from app.core.tenant_context import current_tenant_id, is_bypass
from app.utils.collected_content import build_staged_rows


class ContentAdmissionError(RuntimeError):
    """Static uncertain-admission boundary; private errors are not propagated."""


def message_cursor(items: list[dict]) -> int:
    ids = [item.get("id") for item in items]
    if not ids or any(type(value) is not str or not value.isascii() or not value.isdecimal() or int(value) <= 0 for value in ids):
        raise ContentAdmissionError("content_identity_invalid")
    return max(map(int, ids))


def cursor_value(value) -> int:
    if value is not None and type(value) not in (str, int):
        raise ContentAdmissionError("content_cursor_invalid")
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        raise ContentAdmissionError("content_cursor_invalid") from None
    if parsed < 0:
        raise ContentAdmissionError("content_cursor_invalid")
    return parsed


async def admit_telegram_items(content: list[dict], source, *, run_id=None, wake_analysis=False) -> int:
    """Own one commit: raw receipt + monotonic cursors + optional replay wakeup.

    This returns measured new raw identities ONLY after the transaction commits.
    Duplicate staged/covered identities are acknowledged without another job.
    """
    from sqlalchemy import select
    from app.core.database import new_session
    from app.models import Source, CollectedItem, Job

    tenant_id = current_tenant_id()
    if is_bypass() or type(tenant_id) is not int or tenant_id <= 0 or getattr(source, "tenant_id", None) != tenant_id:
        raise ContentAdmissionError("content_tenant_invalid")
    if type(source.id) is not int or source.id <= 0:
        raise ContentAdmissionError("content_source_invalid")
    if not content or any(item.get("platform") != "telegram" for item in content):
        raise ContentAdmissionError("content_platform_invalid")
    new_cursor = message_cursor(content)
    if any(item.get("external_id") != f"{source.external_id}_{item['id']}" for item in content):
        raise ContentAdmissionError("content_identity_invalid")
    rows = build_staged_rows(content, source, run_id)
    session = new_session()
    try:
        async with session.begin():
            locked = (await session.execute(select(Source).where(
                Source.id == source.id, Source.tenant_id == tenant_id,
                Source.external_id == str(source.external_id), Source.is_active.is_(True),
            ).with_for_update())).scalar_one_or_none()
            if locked is None:
                raise ContentAdmissionError("content_source_unavailable")
            previous = cursor_value(locked.last_item_id)
            params = dict(locked.params or {})
            pull_cursor = cursor_value(params.get("telegram_l2_last_item_id", previous))
            written = await CollectedItem.objects.admit_items(session, rows)
            if type(written) is not int or written < 0 or written > len(rows):
                raise ContentAdmissionError("content_receipt_invalid")
            # Freeze pull progress before L1 advances the display cursor.
            params["telegram_l2_last_item_id"] = str(pull_cursor if wake_analysis else max(pull_cursor, new_cursor))
            locked.params = params
            locked.last_item_id = str(max(previous, new_cursor))
            locked.last_checked = datetime.now(timezone.utc)
            if wake_analysis and written:
                await Job.objects.enqueue("analyze", {"source_ids": [source.id]}, session=session)
        return written
    except ContentAdmissionError:
        raise
    except Exception:
        raise ContentAdmissionError("content_admission_unconfirmed") from None
    finally:
        await session.close()
