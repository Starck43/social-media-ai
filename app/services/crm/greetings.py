"""Upcoming birthday/anniversary events (7/3/1 day reminders) and AI greeting drafts (P14-T04).

Invariants:
- Upcoming events are computed in the workspace timezone (default `Europe/Moscow`),
  mapping `Feb 29 -> Feb 28` in non-leap years.
- Reminders trigger at `7`, `3`, and `1` days ahead (`REMINDER_DAYS = (7, 3, 1)`).
- `GreetingHistory` enforces uniqueness on `(tenant_id, contact_id, event_type, year)`
  so repeat greetings for the same contact/event/year are prevented; once a greeting
  is `approved` or `sent`, generating a duplicate draft is refused with `409`.
- AI draft generation enforces payer/budget checks and restricts prompt context to
  allowlisted fields (`display_name`, `event_type`, `company_title`, `post`,
  `years_turning`, `tone`). Sending is strictly out of scope until P14-T05.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field
from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.tenant_context import current_tenant_id, is_bypass
from app.integrations.bitrix24.client import Bitrix24Error
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.contacts.core import Contact
from app.models.crm.entity import CrmEntity
from app.models.crm.greeting_history import (
    GREETING_CHANNELS,
    GREETING_EVENT_TYPES,
    GreetingHistory,
)
from app.models.notifications.notification import Notification
from app.schemas.business import BusinessSchema
from app.services.crm.sync import parse_crm_date_field

REMINDER_DAYS: tuple[int, ...] = (7, 3, 1)
DEFAULT_WORKSPACE_TIMEZONE = "Europe/Moscow"
ALLOWLISTED_CONTEXT_KEYS: frozenset[str] = frozenset(
    {"display_name", "event_type", "company_title", "post", "years_turning", "tone"}
)


class GreetingDraftOutput(BusinessSchema):
    """Typed structured output contract for AI greeting draft generation."""

    subject: str = Field(..., min_length=1, max_length=240)
    draft_text: str = Field(..., min_length=1, max_length=8000)
    tone: str = Field(default="warm_professional", max_length=40)
    ai_generated: bool = True


def resolve_workspace_today(
    reference_date: date | datetime | None = None, tz_name: str = DEFAULT_WORKSPACE_TIMEZONE
) -> date:
    """Resolve current date in the workspace timezone."""
    if isinstance(reference_date, datetime):
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = timezone.utc
        return reference_date.astimezone(tz).date()
    if isinstance(reference_date, date):
        return reference_date
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    return datetime.now(tz).date()


def _safe_date_for_year(year: int, month: int, day: int) -> date:
    """Map Feb 29 -> Feb 28 in non-leap years."""
    if month == 2 and day == 29 and not calendar.isleap(year):
        return date(year, 2, 28)
    return date(year, month, day)


def next_occurrence_date(raw_date: str | None, *, today: date) -> tuple[date | None, int | None, int | None]:
    """Compute `(next_event_date, days_until, years_turning)` from `YYYY-MM-DD` or `--MM-DD`."""
    normalized, has_year = parse_crm_date_field(raw_date)
    if not normalized:
        return None, None, None

    if has_year:
        parts = normalized.split("-")
        origin_year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    else:
        origin_year = None
        parts = normalized[2:].split("-")
        month, day = int(parts[0]), int(parts[1])

    try:
        candidate = _safe_date_for_year(today.year, month, day)
        if candidate < today:
            candidate = _safe_date_for_year(today.year + 1, month, day)
    except ValueError:
        return None, None, None

    days_until = (candidate - today).days
    years_turning = (
        (candidate.year - origin_year) if (origin_year is not None and candidate.year >= origin_year) else None
    )
    return candidate, days_until, years_turning


def build_allowlisted_greeting_context(
    *,
    display_name: str,
    event_type: str,
    company_title: str | None = None,
    post: str | None = None,
    years_turning: int | None = None,
    tone: str = "warm_professional",
) -> dict[str, Any]:
    """Build strictly allowlisted contact context for the LLM greeting generator."""
    ctx: dict[str, Any] = {
        "display_name": str(display_name).strip()[:200],
        "event_type": str(event_type).strip(),
        "tone": str(tone or "warm_professional").strip()[:40],
    }
    if company_title and str(company_title).strip():
        ctx["company_title"] = str(company_title).strip()[:200]
    if post and str(post).strip():
        ctx["post"] = str(post).strip()[:120]
    if years_turning is not None and years_turning > 0:
        ctx["years_turning"] = int(years_turning)
    return ctx


def default_template_greeting_generator(context: dict[str, Any]) -> GreetingDraftOutput:
    """Deterministic fallback/structured AI draft builder using only allowlisted context."""
    name = context.get("display_name") or "Уважаемый партнёр"
    event_type = context.get("event_type") or "birthday"
    company = context.get("company_title")
    post = context.get("post")
    tone = str(context.get("tone") or "warm_professional")

    if event_type == "anniversary":
        subject = f"Поздравляем с памятной датой, {name}!"
        company_clause = f" и коллектив «{company}»" if company else ""
        draft_text = (
            f"Здравствуйте, {name}!\n\n"
            f"Примите наши искренние поздравления со знаменательной датой{company_clause}. "
            "Благодарим за надёжное партнёрство и желаем дальнейшего процветания, "
            "устойчивого роста и новых совместных успехов!"
        )
    else:
        subject = f"С днём рождения, {name}!"
        role_clause = f" ({post}{f', {company}' if company else ''})" if post else (f" ({company})" if company else "")
        draft_text = (
            f"Здравствуйте, {name}{role_clause}!\n\n"
            "От всей души поздравляем Вас с днём рождения! Желаем крепкого здоровья, "
            "энергии, благополучия и успешной реализации всех намеченных планов."
        )

    return GreetingDraftOutput(
        subject=subject,
        draft_text=draft_text,
        tone=tone,
        ai_generated=True,
    )


class CrmGreetingsService:
    """Upcoming birthday/anniversary calculation, 7/3/1-day reminders, and AI greeting drafts."""

    def __init__(
        self,
        *,
        llm_generator: Callable[[dict[str, Any]], Any] | None = None,
        budget_checker: Callable[..., Any] | None = None,
    ) -> None:
        self._llm_generator = llm_generator
        self._budget_checker = budget_checker

    async def list_upcoming_events(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        horizon_days: int = 14,
        reminder_days_only: bool = False,
        reference_date: date | datetime | None = None,
        tz_name: str = DEFAULT_WORKSPACE_TIMEZONE,
        emit_notifications: bool = False,
        session=None,
    ) -> dict[str, Any]:
        """Compute upcoming birthdays and anniversaries in the workspace timezone."""
        if is_bypass() or current_tenant_id() != tenant_id:
            raise Bitrix24Error("connection_denied", status_code=403)

        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.list_upcoming_events(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    horizon_days=horizon_days,
                    reminder_days_only=reminder_days_only,
                    reference_date=reference_date,
                    tz_name=tz_name,
                    emit_notifications=emit_notifications,
                    session=owned,
                )

        today = resolve_workspace_today(reference_date, tz_name=tz_name)
        max_days = max(1, min(int(horizon_days), 366))

        contacts = list(
            (
                await session.execute(
                    select(Contact).where(
                        Contact.tenant_id == tenant_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        crm_entities = list(
            (
                await session.execute(
                    select(CrmEntity).where(
                        CrmEntity.tenant_id == tenant_id,
                        CrmEntity.is_deleted_remote.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        history_rows = list(
            (
                await session.execute(
                    select(GreetingHistory).where(
                        GreetingHistory.tenant_id == tenant_id,
                    )
                )
            )
            .scalars()
            .all()
        )

        history_by_key: dict[tuple[int, str, int], GreetingHistory] = {
            (h.contact_id, h.event_type, h.year): h for h in history_rows
        }
        crm_by_contact: dict[int, CrmEntity] = {}
        companies_by_ext: dict[tuple[int, str], str] = {}
        for ent in crm_entities:
            if ent.entity_kind == "company":
                companies_by_ext[(ent.connection_id, ent.external_id)] = ent.title_encrypted
            if ent.contact_id is not None and ent.contact_id not in crm_by_contact:
                crm_by_contact[ent.contact_id] = ent

        items: list[dict[str, Any]] = []
        notifications_created = 0

        for contact in sorted(contacts, key=lambda c: c.id):
            if contact.merged_into_id is not None:
                continue

            linked_crm = crm_by_contact.get(contact.id)
            pf = linked_crm.personal_fields if linked_crm is not None else {}
            company_title = None
            if linked_crm and linked_crm.company_external_id:
                company_title = companies_by_ext.get((linked_crm.connection_id, linked_crm.company_external_id))

            raw_events = (
                ("birthday", contact.birthday_encrypted or pf.get("birthday")),
                ("anniversary", contact.anniversary_encrypted or pf.get("anniversary")),
            )

            for event_type, raw_val in raw_events:
                ev_date, days_until, years_turning = next_occurrence_date(raw_val, today=today)
                if ev_date is None or days_until is None or days_until > max_days:
                    continue
                is_reminder_day = days_until in REMINDER_DAYS
                if reminder_days_only and not is_reminder_day:
                    continue

                hist = history_by_key.get((contact.id, event_type, ev_date.year))
                item = {
                    "contact_id": contact.id,
                    "crm_entity_id": linked_crm.id if linked_crm else None,
                    "display_name": contact.display_name_encrypted,
                    "company_title": company_title,
                    "event_type": event_type,
                    "event_date": ev_date,
                    "year": ev_date.year,
                    "days_until": days_until,
                    "is_reminder_day": is_reminder_day,
                    "reminder_bucket": days_until if is_reminder_day else None,
                    "years_turning": years_turning,
                    "preferred_channel": contact.preferred_channel,
                    "history_id": hist.id if hist else None,
                    "history_status": hist.status if hist else None,
                    "send_enabled": False,
                }
                items.append(item)

                if emit_notifications and is_reminder_day and (hist is None or hist.status == "draft"):
                    session.add(
                        Notification(
                            tenant_id=tenant_id,
                            title=f"Напоминание о событии CRM ({days_until} дн.)",
                            message=f"Через {days_until} дн. наступает событие ({event_type}) для контакта #{contact.id}.",
                            severity="info",
                            related_entity_type="contact",
                            related_entity_id=contact.id,
                            dedup_key=f"crm_greeting:{tenant_id}:{contact.id}:{event_type}:{ev_date.isoformat()}:{days_until}",
                            is_read=False,
                        )
                    )
                    notifications_created += 1

        if notifications_created:
            await session.flush()

        items.sort(key=lambda x: (x["days_until"], x["contact_id"], x["event_type"]))
        return {
            "reference_date": today,
            "timezone": tz_name,
            "reminder_days": list(REMINDER_DAYS),
            "notifications_created": notifications_created,
            "items": items,
        }

    async def generate_greeting_draft(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        contact_id: int,
        event_type: str = "birthday",
        year: int | None = None,
        channel: str | None = None,
        tone: str = "warm_professional",
        reference_date: date | datetime | None = None,
        tz_name: str = DEFAULT_WORKSPACE_TIMEZONE,
        llm_generator: Callable[[dict[str, Any]], Any] | None = None,
        budget_checker: Callable[..., Any] | None = None,
        session=None,
    ) -> dict[str, Any]:
        """Generate or update an AI personalized greeting draft without sending."""
        if is_bypass() or current_tenant_id() != tenant_id:
            raise Bitrix24Error("connection_denied", status_code=403)
        if event_type not in GREETING_EVENT_TYPES:
            raise Bitrix24Error("invalid_event_type", status_code=422)
        if channel is not None and channel not in GREETING_CHANNELS:
            raise Bitrix24Error("invalid_channel", status_code=422)

        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.generate_greeting_draft(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    contact_id=contact_id,
                    event_type=event_type,
                    year=year,
                    channel=channel,
                    tone=tone,
                    reference_date=reference_date,
                    tz_name=tz_name,
                    llm_generator=llm_generator,
                    budget_checker=budget_checker,
                    session=owned,
                )

        contact = (
            await session.execute(
                select(Contact).where(
                    Contact.id == contact_id,
                    Contact.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if contact is None or contact.merged_into_id is not None:
            raise Bitrix24Error("contact_not_found", status_code=404)

        crm_entities = list(
            (
                await session.execute(
                    select(CrmEntity).where(
                        CrmEntity.tenant_id == tenant_id,
                        CrmEntity.is_deleted_remote.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        linked_crm = next((e for e in crm_entities if e.contact_id == contact.id), None)
        pf = linked_crm.personal_fields if linked_crm is not None else {}

        raw_date = (
            (contact.birthday_encrypted or pf.get("birthday"))
            if event_type == "birthday"
            else (contact.anniversary_encrypted or pf.get("anniversary"))
        )
        today = resolve_workspace_today(reference_date, tz_name=tz_name)
        ev_date, _, years_turning = next_occurrence_date(raw_date, today=today)
        if ev_date is None:
            raise Bitrix24Error("event_date_missing", "Contact does not have the requested event date", status_code=422)

        target_year = int(year) if year is not None else ev_date.year
        if ev_date.year != target_year:
            ev_date = _safe_date_for_year(target_year, ev_date.month, ev_date.day)

        existing = (
            await session.execute(
                select(GreetingHistory).where(
                    GreetingHistory.tenant_id == tenant_id,
                    GreetingHistory.contact_id == contact.id,
                    GreetingHistory.event_type == event_type,
                    GreetingHistory.year == target_year,
                )
            )
        ).scalar_one_or_none()

        if existing is not None and existing.status in {"approved", "sent"}:
            raise Bitrix24Error(
                "greeting_already_processed",
                "Greeting for this contact, event, and year has already been approved or sent",
                status_code=409,
            )

        checker = budget_checker or self._budget_checker
        if checker is not None:
            allowed = checker(tenant_id=tenant_id, actor_id=actor_id)
            if hasattr(allowed, "__await__"):
                allowed = await allowed
            if not allowed:
                raise Bitrix24Error("llm_budget_exceeded", "LLM budget check denied greeting draft", status_code=402)

        company_title = None
        if linked_crm and linked_crm.company_external_id:
            company_ent = next(
                (
                    e
                    for e in crm_entities
                    if e.entity_kind == "company"
                    and e.connection_id == linked_crm.connection_id
                    and e.external_id == linked_crm.company_external_id
                ),
                None,
            )
            if company_ent is not None:
                company_title = company_ent.title_encrypted

        allowlisted_ctx = build_allowlisted_greeting_context(
            display_name=contact.display_name_encrypted,
            event_type=event_type,
            company_title=company_title,
            post=pf.get("post"),
            years_turning=years_turning,
            tone=tone,
        )
        assert set(allowlisted_ctx.keys()) <= ALLOWLISTED_CONTEXT_KEYS

        generator = llm_generator or self._llm_generator or default_template_greeting_generator
        raw_out = generator(allowlisted_ctx)
        if hasattr(raw_out, "__await__"):
            raw_out = await raw_out
        draft_out = GreetingDraftOutput.model_validate(raw_out)

        chosen_channel = channel or contact.preferred_channel or "email"
        if existing is None:
            row = GreetingHistory(
                tenant_id=tenant_id,
                contact_id=contact.id,
                event_type=event_type,
                event_date=ev_date,
                year=target_year,
                channel=chosen_channel,
                status="draft",
                ai_generated=True,
                subject_encrypted=draft_out.subject,
                draft_text_encrypted=draft_out.draft_text,
            )
            session.add(row)
            await session.flush()
        else:
            existing.event_date = ev_date
            existing.channel = chosen_channel
            existing.status = "draft"
            existing.ai_generated = True
            existing.subject_encrypted = draft_out.subject
            existing.draft_text_encrypted = draft_out.draft_text
            await session.flush()
            row = existing

        await ActionAuditEvent.objects.record_success(
            session=session,
            actor_kind="user" if actor_id > 0 else "service",
            actor_id=actor_id if actor_id > 0 else "task_runner",
            action="comms.campaign_create",
            resource_kind="campaign",
            resource_id=row.id,
        )

        return {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "contact_id": row.contact_id,
            "display_name": contact.display_name_encrypted,
            "event_type": row.event_type,
            "event_date": row.event_date,
            "year": row.year,
            "channel": row.channel,
            "status": row.status,
            "ai_generated": row.ai_generated,
            "subject": row.subject_encrypted,
            "draft_text": row.draft_text_encrypted,
            "send_enabled": False,
        }

    async def send_greeting(self, **kwargs: Any) -> dict[str, Any]:
        """Schedule an approved greeting through the P15-T05 outbox with D15 consent enforcement (P14-T05)."""
        from app.services.crm.messaging import crm_messaging_service

        return await crm_messaging_service.send_greeting(**kwargs)

    async def get_greeting_delivery_status(self, **kwargs: Any) -> dict[str, Any]:
        """Synchronize and return greeting delivery status from the P15-T05 outbox (P14-T05)."""
        from app.services.crm.messaging import crm_messaging_service

        return await crm_messaging_service.get_greeting_delivery_status(**kwargs)


crm_greetings_service = CrmGreetingsService()
