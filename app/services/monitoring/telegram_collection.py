"""Dark-launch personal Telegram collection; no legacy credential fallback.

Only ordinary acquired collect jobs with enqueue-time pins can enter. Selection,
raw/cursor/ledger admission share the runtime transaction; the account guard is
released BEFORE any AI work. Enablement remains a separate rollout gate.
"""

from __future__ import annotations

from app.core.config import settings
from app.jobs.progress import current_claim
from app.services.monitoring.selection import SelectionScope, select_for_collection
from app.services.monitoring.selection_store import source_limit
from app.services.monitoring.staging import _admit_runtime_telegram_batch
from app.services.social.connector_authorizations import AuthorizationUnavailable
from app.services.social.credentials import AuthorizationRequired
from app.services.social.telegram_grant_access import source_grant_selection
from app.services.social.telegram_runtime import _telegram_history_batch


async def collect_personal_telegram(
    collector,
    source,
    *,
    content_type="posts",
    analyze=True,
    force_reanalyze=False,
    run_id=None,
    dry_run=False,
    selection_scope=None,
    task_id=None,
    collection_context=None,
    seen_items=None,
    report_empty=False,
):
    if not settings.TELEGRAM_ACCOUNT_RUNTIME_ENABLED:
        raise AuthorizationRequired(
            "Личный сбор Telegram ещё не включён",
            platform="telegram",
            hint="Нужны итоговые проверки и отдельное разрешение на включение. Перехода к старой сессии нет.",
        )
    claim = current_claim()
    if (
        claim is None
        or claim.job_type != "collect"
        or content_type not in ("posts", "messages")
        or (run_id is not None and run_id != claim.job_id)
        or (task_id is not None and task_id != claim.agent_task_id)
    ):
        raise AuthorizationUnavailable()
    from app.services.ai.dedup import item_hash
    from app.services.collection.planner import item_identity
    from app.services.monitoring.collector import _collection_summary

    selected_source = source_grant_selection(source)
    task_limit = getattr(getattr(collection_context, "intent", None), "limit", None)
    limit = min(source_limit(source, task_limit), 1000)
    claimed = set()
    duplicates = 0
    async with _telegram_history_batch(selected_source, claim, source, limit=limit) as batch:
        # Private trusted orchestration only; raw values never escape to an API.
        # Batch retention is bounded by the same owner/lifetime as admission.
        content = list(batch._items)
        result = select_for_collection(content, selection_scope or SelectionScope())
        selection_by_hash = {
            item_hash(decision.item): (decision.status, decision.reason) for decision in result.decisions
        }
        analysis_content = []
        selected_ids = {id(item) for item in result.selected}
        eligible_ids = selected_ids | {id(item) for item in result.needs_review}
        new_content = []
        counted = set()
        for item in content:
            identity = item_identity(item)
            if seen_items is None or identity is None or (identity not in seen_items and identity not in counted):
                new_content.append(item)
                if identity is not None:
                    counted.add(identity)
            if id(item) not in eligible_ids:
                continue
            if seen_items is not None and identity is not None and (identity in seen_items or identity in claimed):
                duplicates += 1
                selection_by_hash[item_hash(item)] = ("rejected", "duplicate_in_run")
                continue
            if identity is not None:
                claimed.add(identity)
            if id(item) in selected_ids:
                analysis_content.append(item)
        new_count = await collector._count_new_items(new_content, source) if content else 0
        if dry_run:
            # Linearize metadata-only rehearsal output through the same checks,
            # but write no raw rows, cursor, selection ledger or analysis wakeup.
            # Only runtime epoch/job metadata may be written. Never put fetched
            # text/hashes in a deferred, unfenced Job.result preview.
            async with batch._admission():
                pass
            staged = 0
        else:
            staged = await _admit_runtime_telegram_batch(
                batch,
                source,
                selection=selection_by_hash,
                collection_selection=result,
                limit_count=limit,
                limit_reached=len(content) >= limit,
            )
        hashes = [] if dry_run else [item_hash(item) for item in content]
    # Account/secret/runtime scopes have exited. AI has its own existing
    # payer/model/usage admission; this fence never reserves or spends AI funds.
    if seen_items is not None and not dry_run:
        seen_items.update(claimed)
    analytics = None
    analysis_errors = None
    if analyze and not dry_run and analysis_content:
        before = getattr(collector.analyzer, "reported_errors", None)
        analytics = await collector.analyzer.analyze_content(analysis_content, source, force_reanalyze=force_reanalyze)
        after = getattr(collector.analyzer, "reported_errors", None)
        if type(before) is int and before >= 0 and type(after) is int and after >= before:
            analysis_errors = after - before
        await collector._retire_staged(source, analytics)
    if not content and not report_empty:
        return None
    response = {
        "source_id": source.id,
        "content_count": len(content),
        "parent_count": len(content),
        "limit_count": limit,
        "limit_reached": len(content) >= limit,
        "duplicate_count": duplicates,
        "new_items": new_count,
        "analyzed": bool(analyze and not dry_run and analysis_content),
        "analytics_count": len(analytics) if analytics else 0,
        "staged": staged,
        "selected": len(result.selected),
        "needs_review": len(result.needs_review),
        "rejected": len(result.rejected),
        "content_hashes": hashes,
        "selection": {
            "eligible": len(result.selected),
            "needs_review": len(result.needs_review),
            "rejected": len(result.rejected),
            "reasons": dict(result.reasons),
            "unknown_tracked_users": list(result.unknown_tracked_users),
        },
        "comments": None,
        "candidates": None,
        "profiles": None,
        "collection": _collection_summary(collection_context),
        "telegram_access": "personal_account_grant",
    }
    if not content:
        response["empty"] = True
    if dry_run:
        response.update(dry_run=True, preview=[], preview_omitted="telegram_rehearsal_metadata_only")
    if analysis_errors is not None:
        response["analysis_errors"] = analysis_errors
    return response
