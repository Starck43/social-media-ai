"""Shared Telegram raw admission; never call an analyzer or sender here."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.tenant_context import current_tenant_id, is_bypass
from app.utils.collected_content import build_staged_rows


class ContentAdmissionError(RuntimeError):
    """Static uncertain-admission boundary; private errors are not propagated."""


def message_cursor(items: list[dict]) -> int:
    ids = [item.get("id") for item in items]
    if not ids or any(
        type(value) is not str or not value.isascii() or not value.isdecimal() or int(value) <= 0 for value in ids
    ):
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


async def _write_telegram_items(
    session,
    content: list[dict],
    source,
    *,
    run_id=None,
    wake_analysis=False,
    selection: dict[str, tuple[str, str | None]] | None = None,
) -> int:
    """Write inside the CALLER'S transaction; result is NOT a committed receipt.

    Never open/commit/close a second session here. Runtime consent/epoch/job
    locks must be held in this exact transaction. The caller owns the commit and
    must not report success if its outcome is unknown.
    """
    from sqlalchemy import select

    from app.models import CollectedItem, Job, Source

    tenant_id = current_tenant_id()
    if not session.in_transaction() or not session.is_active:
        raise ContentAdmissionError("content_transaction_invalid")
    if is_bypass() or type(tenant_id) is not int or tenant_id <= 0 or getattr(source, "tenant_id", None) != tenant_id:
        raise ContentAdmissionError("content_tenant_invalid")
    if type(source.id) is not int or source.id <= 0:
        raise ContentAdmissionError("content_source_invalid")
    if (
        type(content) is not list
        or not content
        or any(type(item) is not dict or item.get("platform") != "telegram" for item in content)
    ):
        raise ContentAdmissionError("content_platform_invalid")
    new_cursor = message_cursor(content)
    if any(item.get("external_id") != f"{source.external_id}_{item['id']}" for item in content):
        raise ContentAdmissionError("content_identity_invalid")
    rows = [
        row
        for row in build_staged_rows(content, source, run_id, selection=selection)
        if row.get("selection_status") in ("selected", "needs_review")
    ]
    locked = (
        await session.execute(
            select(Source)
            .where(
                Source.id == source.id,
                Source.tenant_id == tenant_id,
                Source.external_id == str(source.external_id),
                Source.is_active.is_(True),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if locked is None:
        raise ContentAdmissionError("content_source_unavailable")
    previous = cursor_value(locked.last_item_id)
    params = dict(locked.params or {})
    # Bot API writes own last_item_id; an absent personal MTProto cursor must
    # begin at zero rather than inherit the Bot high-water mark.
    pull_cursor = cursor_value(params.get("telegram_l2_last_item_id", "0"))
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
    # Persist/validate changes on THIS transaction before the runtime gate's
    # final rechecks. No implicit autoflush assumption or early acknowledgement.
    await session.flush()
    return written


async def admit_telegram_items(
    content: list[dict],
    source,
    *,
    run_id=None,
    wake_analysis=False,
    selection: dict[str, tuple[str, str | None]] | None = None,
) -> int:
    """Legacy/push owning wrapper; not a personal-runtime authorization gate.

    Keep the existing one-commit contract. Runtime pull MUST use the private
    batch helper below instead, never check permission then call this wrapper.
    """
    from app.core.database import new_session

    session = new_session()
    try:
        async with session.begin():
            written = await _write_telegram_items(
                session, content, source, run_id=run_id, wake_analysis=wake_analysis, selection=selection
            )
        return written
    except ContentAdmissionError:
        raise
    except Exception:
        raise ContentAdmissionError("content_admission_unconfirmed") from None
    finally:
        await session.close()


async def _admit_runtime_telegram_batch(
    batch,
    source,
    *,
    selection: dict[str, tuple[str, str | None]] | None = None,
    collection_selection=None,
    limit_count=None,
    limit_reached=False,
) -> int:
    """One batch/one attempt; acknowledge ONLY after its fenced commit.

    There is deliberately no supplied content, session, run_id or wake-analysis
    escape. The batch carries the fetched items and acquired job identity; a
    later model phase must use the existing payer/usage admission independently.
    """
    from app.services.social.connector_authorizations import AuthorizationUnavailable
    from app.services.social.telegram_grant_access import source_grant_selection
    from app.services.social.telegram_runtime import _TelegramReadBatch

    if type(batch) is not _TelegramReadBatch or source_grant_selection(source) != batch._fence._pin.selection:
        raise AuthorizationUnavailable()
    async with batch._admission() as (session, items):
        if items:
            written = await _write_telegram_items(
                session, list(items), source, run_id=batch._fence._pin.claim.job_id, selection=selection
            )
        else:
            # Empty responses still pass the consent/job/epoch gate, without
            # falsely advancing cursors or waking another job.
            written = 0
        if collection_selection is not None:
            from app.services.monitoring.selection_store import _write_collection_selection

            await _write_collection_selection(
                session,
                source,
                list(items),
                collection_selection,
                run_id=batch._fence._pin.claim.job_id,
                task_id=batch._fence._pin.claim.agent_task_id,
                limit_count=limit_count,
                limit_reached=limit_reached,
            )
    return written
