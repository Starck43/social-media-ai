"""Read-only, actor-scoped morning brief over the existing reporting window.

No provider/model calls, no persistence and no implicit delivery. Future providers
must implement the attention contract and retain their own authorization boundary.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from app.services.attention.contracts import AttentionItem
from app.services.social.triage import SocialEvidence, rank

SOURCE_LIMIT = 100
ROW_LIMIT = 500


@dataclass(frozen=True)
class MorningBrief:
    tenant_id: int
    start: date
    end: date
    timezone: str
    generated_at: datetime
    items: tuple[AttentionItem, ...]
    notices: tuple[str, ...]
    missing_providers: tuple[str, ...] = ("inbox", "planner", "calendar")
    model_status: str = "disabled_deterministic_only"

    @property
    def top(self) -> tuple[AttentionItem, ...]:
        return self.items[:3]

    @property
    def remaining(self) -> tuple[AttentionItem, ...]:
        return self.items[3:]


def local_window(now: datetime, timezone_name: str, days: int = 1) -> tuple[date, date]:
    from datetime import timedelta

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("brief_aware_time_required")
    if type(days) is not int or not 1 <= days <= 31:
        raise ValueError("brief_window_invalid")
    end = now.astimezone(ZoneInfo(timezone_name)).date()
    return end - timedelta(days=days - 1), end


def require_actor(tenant_id: int, user_id: int):
    from app.core.permissions import get_current_user, has_permission
    from app.core.tenant_context import current_tenant_id, is_bypass
    from app.types import ActionType

    actor = get_current_user()
    if (
        is_bypass()
        or current_tenant_id() != tenant_id
        or type(tenant_id) is not int
        or tenant_id <= 0
        or actor is None
        or actor.id != user_id
        or not all(has_permission(actor, model, ActionType.VIEW) for model in ("source", "aianalytics"))
    ):
        raise PermissionError("social_brief_denied")
    return actor


class SocialBriefRepository:
    async def sources(self, tenant_id):
        from app.models import Source

        return list(
            await Source.objects.filter(tenant_id=tenant_id)
            .select_related("platform")
            .order_by(Source.id.asc())
            .limit(SOURCE_LIMIT + 1)
        )

    async def access(self, sources):
        from app.services.social.source_access import resolve_access

        return await resolve_access(sources)

    async def rows(self, tenant_id, source_ids, start, end):
        from app.models import AIAnalytics
        from app.types import PeriodType

        # An empty source set must never become the report's all-sources fallback.
        if not source_ids:
            return []
        return list(
            await AIAnalytics.objects.filter(
                tenant_id=tenant_id,
                source_id__in=sorted(source_ids),
                analysis_date__gte=start,
                analysis_date__lte=end,
                period_type=PeriodType.DAY,
            )
            .order_by(AIAnalytics.analysis_date.desc(), AIAnalytics.id.desc())
            .limit(ROW_LIMIT + 1)
        )

    async def timezone(self, tenant_id):
        from app.models import Tenant
        from app.tasks.cron import resolve_tz

        tenant = await Tenant.objects.get(id=tenant_id)
        return resolve_tz(tenant)


async def preview(
    tenant_id: int,
    user_id: int,
    *,
    days: int = 1,
    now: datetime | None = None,
    repository=None,
    overrides: dict[str, str] | None = None,
) -> MorningBrief:
    from dataclasses import replace

    from app.services.ai.analysis_render import analysis_coverage, render_analysis
    from app.services.social.connections import _platform_key

    require_actor(tenant_id, user_id)
    repo = repository or SocialBriefRepository()
    now = now or datetime.now(timezone.utc)
    zone = await repo.timezone(tenant_id)
    start, end = local_window(now, zone, days)
    notices = ["Охват ограничен сохранёнными результатами; отсутствие данных не означает отсутствие публикаций."]
    sources = await repo.sources(tenant_id)
    if len(sources) > SOURCE_LIMIT:
        notices.append("Список источников ограничен; часть источников не включена.")
    sources = [
        source
        for source in sources[:SOURCE_LIMIT]
        if source.tenant_id == tenant_id and _platform_key(source) in {"vk", "telegram"}
    ]
    access = await repo.access(sources)
    allowed = set()
    for source in sources:
        status = access.get(source.id)
        if not source.is_active or status is None or status.blocked:
            notices.append("Источник недоступен или доступ отозван; его свидетельства исключены.")
            continue
        allowed.add(source.id)
        if status.service_only:
            notices.append("Часть источников использует общий доступ; личный охват не подтверждён.")
    rows = await repo.rows(tenant_id, allowed, start, end)
    if len(rows) > ROW_LIMIT:
        notices.append("Показана ограниченная выборка результатов; глобальный рейтинг не гарантирован.")
    # Re-read source status after rows are read, before projecting any source text.
    refreshed = await repo.sources(tenant_id)
    fresh_sources = {source.id: source for source in refreshed if source.tenant_id == tenant_id}
    fresh_access = await repo.access(list(fresh_sources.values()))
    allowed = frozenset(
        sid
        for sid in allowed
        if sid in fresh_sources
        and fresh_sources[sid].is_active
        and sid in fresh_access
        and not fresh_access[sid].blocked
    )
    if allowed != frozenset(
        row.id for row in sources if row.is_active and row.id in access and not access[row.id].blocked
    ):
        notices.append("Доступ к части источников изменился; их свидетельства исключены.")
    require_actor(tenant_id, user_id)
    evidence = []
    for row in rows[:ROW_LIMIT]:
        if row.tenant_id != tenant_id or row.source_id not in allowed or not start <= row.analysis_date <= end:
            continue
        summary = row.summary_data if isinstance(row.summary_data, dict) else {}
        title = render_analysis(summary).get("analysis_title")
        title = title if isinstance(title, str) else ""
        if analysis_coverage(summary)["incomplete"]:
            notices.append("Часть анализов неполна; проверьте ограничения в свидетельствах.")
        # analysis_date is the authoritative reporting date; do not invent a post timestamp.
        observed = datetime.combine(row.analysis_date, time.min, ZoneInfo(zone))
        evidence.append(SocialEvidence(tenant_id, row.source_id, row.id, title, observed))
    items = rank(evidence, tenant_id=tenant_id, allowed_source_ids=allowed)
    corrected = []
    for item in items:
        category = (overrides or {}).get(item.authoritative_ref)
        if category in {"needs_attention", "can_wait", "uncertain"}:
            item = replace(
                item,
                category=category,
                recommended_priority={"needs_attention": 80, "uncertain": 50, "can_wait": 20}[category],
                reason="Приоритет исправлен пользователем в текущей сессии.",
                priority_basis="user_override_session",
            )
        corrected.append(item)
    corrected.sort(key=lambda item: (-item.recommended_priority, -item.freshness.timestamp(), item.authoritative_ref))
    if not items:
        notices.append("За выбранное окно нет доступных социальных результатов.")
    return MorningBrief(tenant_id, start, end, zone, now, tuple(corrected), tuple(dict.fromkeys(notices)))
