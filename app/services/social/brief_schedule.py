"""User-configured morning briefs on the existing AgentTask and D18 machinery.

Persist only authenticated schedule decisions and generic signed links. Provider
records remain authoritative; collection, model calls and direct transports are
outside this service. Shared notifications own external delivery and quiet hours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.tasks.cron import next_run_at

UTC = timezone.utc
BRIEF_LINK = "/app/digests/morning"


def occurrence_token(tenant_id: int, task_id: int, config_token: str, scheduled_for: datetime) -> str:
    """Sign the occurrence timestamp at job admission so callers cannot edit its window."""
    from itsdangerous import URLSafeSerializer

    from app.core.config import settings

    if (
        type(tenant_id) is not int
        or tenant_id <= 0
        or type(task_id) is not int
        or task_id <= 0
        or not isinstance(config_token, str)
        or not config_token
        or scheduled_for.tzinfo is None
        or scheduled_for.utcoffset() is None
    ):
        raise ValueError("brief_occurrence_invalid")
    return URLSafeSerializer(settings.SECRET_KEY, salt="social-morning-brief-occurrence-v1").dumps(
        {
            "tenant_id": tenant_id,
            "task_id": task_id,
            "config_token": config_token,
            "scheduled_for": scheduled_for.astimezone(UTC).isoformat(),
        }
    )


def verify_occurrence_token(
    token: str,
    *,
    tenant_id: int,
    task_id: int,
    config_token: str,
    scheduled_for: datetime,
) -> bool:
    from itsdangerous import BadSignature, URLSafeSerializer

    from app.core.config import settings

    try:
        decoded = URLSafeSerializer(settings.SECRET_KEY, salt="social-morning-brief-occurrence-v1").loads(token)
    except (BadSignature, TypeError):
        return False
    expected = {
        "tenant_id": tenant_id,
        "task_id": task_id,
        "config_token": config_token,
        "scheduled_for": scheduled_for.astimezone(UTC).isoformat(),
    }
    return decoded == expected


@dataclass(frozen=True)
class MorningBriefSchedule:
    tenant_id: int
    user_id: int
    local_time: str
    timezone: str
    days: int
    channel: str = "inbox"
    notify_confirmed: bool = False

    @property
    def cron_expr(self) -> str:
        hour, minute = map(int, self.local_time.split(":"))
        return f"{minute} {hour} * * *"

    def as_payload(self) -> dict[str, object]:
        """JSON-safe schedule metadata suitable for a task-owned config field."""
        return {
            "kind": "social_morning_brief",
            "user_id": self.user_id,
            "local_time": self.local_time,
            "timezone": self.timezone,
            "days": self.days,
            "channel": self.channel,
            "notify_confirmed": self.notify_confirmed,
        }

    def signed_payload(self, task_id: int) -> dict[str, str]:
        """Bind task config to its workspace and recipient with the app signer."""
        if type(task_id) is not int or task_id <= 0:
            raise ValueError("brief_schedule_task_invalid")
        validated = configure(
            self.tenant_id,
            self.user_id,
            local_time=self.local_time,
            timezone_name=self.timezone,
            days=self.days,
            channel=self.channel,
            notify_confirmed=self.notify_confirmed,
        )
        require_configuring_actor(validated)
        from itsdangerous import URLSafeSerializer

        from app.core.config import settings

        token = URLSafeSerializer(settings.SECRET_KEY, salt="social-morning-brief-schedule-v1").dumps(
            {"tenant_id": validated.tenant_id, "task_id": task_id, **validated.as_payload()}
        )
        return {"kind": "social_morning_brief", "signed_config": token}

    @classmethod
    def from_signed_payload(cls, tenant_id: int, task_id: int, payload: object) -> "MorningBriefSchedule":
        """Verify the immutable task/recipient/config binding before dispatch."""
        if (
            type(tenant_id) is not int
            or tenant_id <= 0
            or type(task_id) is not int
            or task_id <= 0
            or not isinstance(payload, dict)
            or payload.get("kind") != "social_morning_brief"
            or not isinstance(payload.get("signed_config"), str)
            or set(payload) != {"kind", "signed_config"}
        ):
            raise ValueError("brief_schedule_config_invalid")
        from itsdangerous import BadSignature, URLSafeSerializer

        from app.core.config import settings

        signer = URLSafeSerializer(settings.SECRET_KEY, salt="social-morning-brief-schedule-v1")
        try:
            decoded = signer.loads(payload["signed_config"])
        except BadSignature:
            raise ValueError("brief_schedule_config_invalid") from None
        if (
            not isinstance(decoded, dict)
            or set(decoded)
            != {
                "tenant_id",
                "task_id",
                "kind",
                "user_id",
                "local_time",
                "timezone",
                "days",
                "channel",
                "notify_confirmed",
            }
            or decoded.get("kind") != "social_morning_brief"
            or decoded.get("tenant_id") != tenant_id
            or decoded.get("task_id") != task_id
        ):
            raise ValueError("brief_schedule_config_invalid")
        return configure(
            tenant_id,
            decoded.get("user_id"),
            local_time=decoded.get("local_time"),
            timezone_name=decoded.get("timezone"),
            days=decoded.get("days"),
            channel=decoded.get("channel", "inbox"),
            notify_confirmed=decoded.get("notify_confirmed"),
        )


def require_configuring_actor(schedule: MorningBriefSchedule) -> None:
    """Require the recipient to configure their own brief in this workspace."""
    from app.core.permissions import get_current_user, has_permission
    from app.core.tenant_context import current_tenant_id, is_bypass
    from app.types import ActionType

    actor = get_current_user()
    if (
        is_bypass()
        or current_tenant_id() != schedule.tenant_id
        or actor is None
        or actor.id != schedule.user_id
        or not all(has_permission(actor, model, ActionType.VIEW) for model in ("source", "aianalytics"))
        or not has_permission(actor, "agenttask", ActionType.CREATE)
        or not has_permission(actor, "agenttask", ActionType.UPDATE)
    ):
        raise PermissionError("social_brief_schedule_denied")


def schedule_for_task(task) -> MorningBriefSchedule:
    """Verify a current AgentTask definition matches its signed schedule."""
    if getattr(task, "job_type", None) != "social.brief":
        raise ValueError("brief_schedule_task_type_invalid")
    schedule = MorningBriefSchedule.from_signed_payload(task.tenant_id, task.id, task.payload)
    if getattr(task, "cron_expr", None) != schedule.cron_expr:
        raise ValueError("brief_schedule_cron_mismatch")
    return schedule


async def configure_schedule(
    tenant_id: int,
    user_id: int,
    *,
    local_time: str,
    timezone_name: str,
    days: int = 1,
    channel: str = "inbox",
    notify_confirmed: bool = False,
    now: datetime | None = None,
):
    """Create or update the recipient's single signed scheduled brief task.

    The task row is created inactive and signed in the same transaction after
    PostgreSQL assigns its ID. A conflicting or invalid task is never adopted.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.core.database import async_session_maker
    from app.core.tenant_context import current_tenant_id
    from app.models import AgentTask
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.models.managers.job_manager import JobManager

    schedule = configure(
        tenant_id,
        user_id,
        local_time=local_time,
        timezone_name=timezone_name,
        days=days,
        channel=channel,
        notify_confirmed=notify_confirmed,
    )
    require_configuring_actor(schedule)
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("brief_schedule_aware_time_required")
    if current_tenant_id() != tenant_id:
        raise PermissionError("social_brief_schedule_denied")
    task_name = f"Morning social brief [{user_id}]"
    next_at = next_occurrence(schedule, after=now)
    async with async_session_maker() as session:
        try:
            await JobManager.lock_source_admission(session, tenant_id)
            named = await session.scalar(
                select(AgentTask).where(AgentTask.tenant_id == tenant_id, AgentTask.name == task_name).with_for_update()
            )
            task = None
            if named is not None:
                if named.job_type != "social.brief":
                    raise ValueError("brief_schedule_task_conflict")
                try:
                    existing = schedule_for_task(named)
                except ValueError:
                    raise ValueError("brief_schedule_task_conflict") from None
                if existing.user_id != user_id:
                    raise ValueError("brief_schedule_task_conflict")
                task = named
                await JobManager().delete_pending_for_task(session, tenant_id=tenant_id, task_id=task.id)
                task.cron_expr = schedule.cron_expr
                task.payload = schedule.signed_payload(task.id)
                task.is_active = True
                task.next_run_at = next_at
            else:
                # An existing signed schedule renamed by an editor is found by
                # task type and verified while locked; never create a second one.
                candidates = list(
                    (
                        await session.scalars(
                            select(AgentTask)
                            .where(AgentTask.tenant_id == tenant_id, AgentTask.job_type == "social.brief")
                            .order_by(AgentTask.id)
                            .with_for_update()
                        )
                    ).all()
                )
                matching = []
                for candidate in candidates:
                    try:
                        existing = schedule_for_task(candidate)
                    except ValueError:
                        continue
                    if existing.user_id == user_id:
                        matching.append(candidate)
                if matching:
                    if len(matching) != 1:
                        raise ValueError("brief_schedule_duplicate_tasks")
                    task = matching[0]
                    await JobManager().delete_pending_for_task(session, tenant_id=tenant_id, task_id=task.id)
                    task.name = task_name
                    task.cron_expr = schedule.cron_expr
                    task.payload = schedule.signed_payload(task.id)
                    task.is_active = True
                    task.next_run_at = next_at
                else:
                    manager = AgentTaskManager()
                    values = await manager._apply_tenant(
                        session,
                        {
                            "name": task_name,
                            "cron_expr": schedule.cron_expr,
                            "job_type": "social.brief",
                            "payload": {"kind": "social_morning_brief", "pending_setup": True},
                            "is_active": False,
                            "next_run_at": None,
                        },
                    )
                    await manager._validate_scenario_binding(session, tenant_id, None)
                    task = AgentTask(**values)
                    session.add(task)
                    await session.flush()
                    task.payload = schedule.signed_payload(task.id)
                    task.is_active = True
                    task.next_run_at = next_at
            await session.flush()
            await session.commit()
            return task
        except IntegrityError:
            await session.rollback()
            raise ValueError("brief_schedule_task_conflict") from None
        except Exception:
            await session.rollback()
            raise


def configure(
    tenant_id: int,
    user_id: int,
    *,
    local_time: str,
    timezone_name: str,
    days: int = 1,
    channel: str = "inbox",
    notify_confirmed: bool = False,
) -> MorningBriefSchedule:
    """Validate a user's local wall-clock schedule and reporting window."""
    if type(tenant_id) is not int or tenant_id <= 0 or type(user_id) is not int or user_id <= 0:
        raise ValueError("brief_schedule_identity_invalid")
    if (
        not isinstance(local_time, str)
        or len(local_time) != 5
        or local_time[2] != ":"
        or not local_time[:2].isdigit()
        or not local_time[3:].isdigit()
    ):
        raise ValueError("brief_schedule_time_invalid")
    hour, minute = int(local_time[:2]), int(local_time[3:])
    if hour > 23 or minute > 59:
        raise ValueError("brief_schedule_time_invalid")
    if not isinstance(timezone_name, str) or not timezone_name:
        raise ValueError("brief_schedule_timezone_invalid")
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("brief_schedule_timezone_invalid") from None
    if type(days) is not int or not 1 <= days <= 31:
        raise ValueError("brief_schedule_window_invalid")
    if not isinstance(channel, str) or channel not in {"inbox", "telegram", "max"}:
        raise ValueError("brief_schedule_channel_invalid")
    if type(notify_confirmed) is not bool or (channel != "inbox" and not notify_confirmed):
        raise ValueError("brief_schedule_external_confirmation_required")
    return MorningBriefSchedule(tenant_id, user_id, local_time, timezone_name, days, channel, notify_confirmed)


def next_occurrence(schedule: MorningBriefSchedule, *, after: datetime) -> datetime:
    """Return the next UTC occurrence using the existing cron/DST policy."""
    if after.tzinfo is None or after.utcoffset() is None:
        raise ValueError("brief_schedule_aware_time_required")
    return next_run_at(schedule.cron_expr, schedule.timezone, after.astimezone(UTC))


async def delivery_window(session, user_id: int, timezone_name: str, *, now: datetime):
    """Reuse D18's preference and quiet-hours policy for a brief recipient."""
    from app.services.notifications.reminders import reminder_notifications

    return await reminder_notifications.allowed_at(session, user_id, timezone_name, now)


def idempotency_key(schedule: MorningBriefSchedule, *, start, end) -> str:
    """Stable per-user, per-window key for durable queue/receipt deduplication."""
    material = f"social-morning-brief:v1:{schedule.tenant_id}:{schedule.user_id}:{start}:{end}"
    return "social-morning-brief:" + sha256(material.encode("utf-8")).hexdigest()


def verify_report_token(token: str, *, task, user_id: int) -> tuple[date, date] | None:
    """Verify the signed frozen window embedded in an inbox/external link."""
    from itsdangerous import BadSignature, URLSafeSerializer

    from app.core.config import settings

    if not isinstance(token, str) or len(token) > 2048:
        return None
    try:
        value = URLSafeSerializer(settings.SECRET_KEY, salt="social-morning-brief-window-v1").loads(token)
    except BadSignature:
        return None
    try:
        schedule = schedule_for_task(task)
    except ValueError:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"tenant_id", "task_id", "user_id", "schedule_token", "start", "end"}
        or value["tenant_id"] != task.tenant_id
        or value["task_id"] != task.id
        or value["user_id"] != user_id
        or value["schedule_token"] != task.payload.get("signed_config")
    ):
        return None
    try:
        start, end = date.fromisoformat(value["start"]), date.fromisoformat(value["end"])
    except (TypeError, ValueError):
        return None
    if (end - start).days != schedule.days - 1:
        return None
    return start, end


def report_window_from_message(event, *, task, user_id: int) -> tuple[date, date] | None:
    """Extract and verify the server-signed window from a generic link message."""
    from urllib.parse import parse_qs

    message_link = (event.message or "").rsplit(" ", 1)[-1]
    if len(message_link) > 4096:
        return None
    try:
        parsed = urlsplit(message_link)
        tokens = parse_qs(parsed.query).get("brief_token", [])
    except ValueError:
        return None
    if parsed.path != BRIEF_LINK or len(tokens) != 1:
        return None
    return verify_report_token(tokens[0], task=task, user_id=user_id)


def _public_url(path: str) -> str | None:
    from app.core.config import settings

    base = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    try:
        parsed = urlsplit(base) if base else None
        port = None if parsed is None else parsed.port
    except ValueError:
        return None
    if (
        parsed is None
        or any(ord(char) <= 32 or ord(char) == 127 for char in base)
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        return None
    return f"{parsed.scheme}://{parsed.netloc}{path}"


async def prepare_dispatch(
    schedule: MorningBriefSchedule,
    *,
    task_id: int,
    task_config_token: str,
    scheduled_for: datetime,
) -> dict:
    """Reauthorize and prepare generic link-only content for a worker dispatch.

    ``preview`` rechecks the current actor and source rights, including at
    dispatch time. This function returns no evidence text and grants no send
    permission; the caller still needs a separately authorized notification
    delivery record and durable sender.
    """
    from app.services.social.morning_brief import preview

    if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
        raise ValueError("brief_schedule_aware_time_required")
    brief = await preview(
        schedule.tenant_id,
        schedule.user_id,
        days=schedule.days,
        now=scheduled_for,
        timezone_name=schedule.timezone,
    )
    link = (
        BRIEF_LINK
        + "?"
        + urlencode(
            {
                "tz": schedule.timezone,
                "days": str(schedule.days),
                "start": brief.start.isoformat(),
                "end": brief.end.isoformat(),
            }
        )
    )
    from itsdangerous import URLSafeSerializer

    from app.core.config import settings

    window_token = URLSafeSerializer(settings.SECRET_KEY, salt="social-morning-brief-window-v1").dumps(
        {
            "tenant_id": schedule.tenant_id,
            "task_id": task_id,
            "user_id": schedule.user_id,
            "schedule_token": task_config_token,
            "start": brief.start.isoformat(),
            "end": brief.end.isoformat(),
        }
    )
    link = f"{link}&brief_token={urlencode({'token': window_token}).split('=', 1)[1]}"
    external_link = _public_url(link)
    return {
        "title": "Утренний обзор готов",
        "message": f"Откройте обзор: {external_link or link}",
        "link": link,
        "external_link_available": external_link is not None,
        "dedup_key": idempotency_key(schedule, start=brief.start, end=brief.end),
        "generated_at": brief.generated_at,
    }


async def publish_dispatch(
    schedule: MorningBriefSchedule,
    *,
    task_id: int,
    task_config_token: str,
    scheduled_for: datetime,
) -> int:
    """Commit a generic inbox event and optional D18 delivery ledger row."""
    from sqlalchemy.dialects.postgresql import insert

    from app.core.database import async_session_maker
    from app.models import NotificationDelivery, NotificationReceipt
    from app.types import NotificationType

    prepared = await prepare_dispatch(
        schedule,
        task_id=task_id,
        task_config_token=task_config_token,
        scheduled_for=scheduled_for,
    )
    instant = datetime.now(UTC)
    async with async_session_maker() as session:
        event = await NotificationReceipt.objects.publish(
            session=session,
            recipient_user_ids=[schedule.user_id],
            title=prepared["title"],
            message=(
                (
                    "Внешняя отправка этого обзора недоступна; откройте его во входящих. "
                    if schedule.channel == "max"
                    else (
                        "Для внешней отправки не настроена ссылка; откройте обзор во входящих. "
                        if schedule.channel == "telegram" and not prepared["external_link_available"]
                        else ""
                    )
                )
                + prepared["message"]
            ),
            notification_type=NotificationType.SYSTEM_UPDATE,
            related_entity_type="social_morning_brief",
            related_entity_id=task_id,
            severity=(
                "warning"
                if schedule.channel == "max"
                or (schedule.channel == "telegram" and not prepared["external_link_available"])
                else "info"
            ),
            dedup_key=prepared["dedup_key"],
            expires_at=instant + timedelta(days=30),
        )
        if schedule.channel != "inbox":
            delivered_channel = "max" if schedule.channel == "max" else "telegram"
            blocked = schedule.channel == "max" or (
                schedule.channel == "telegram" and not prepared["external_link_available"]
            )
            statement = insert(NotificationDelivery).values(
                tenant_id=schedule.tenant_id,
                notification_id=event.id,
                user_id=schedule.user_id,
                channel=delivered_channel,
                state="blocked" if blocked else "pending",
                next_attempt_at=instant,
                error_code=(
                    "channel_not_implemented"
                    if schedule.channel == "max"
                    else "public_base_url_unavailable" if blocked and schedule.channel == "telegram" else None
                ),
            )
            await session.execute(
                statement.on_conflict_do_nothing(
                    index_elements=[
                        NotificationDelivery.notification_id,
                        NotificationDelivery.user_id,
                        NotificationDelivery.channel,
                    ]
                )
            )
        await session.commit()
        return event.id
