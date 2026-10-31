"""Reviewed, atomic workspace deletion with permanent platform evidence."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import get_current_user
from app.core.tenant_context import is_bypass, tenant_scope
from app.models import ActionAuditEvent, Base, PlatformRole, Tenant, User, WorkspaceOffboardingEvent

REASON_LABELS = {
    "owner_request": "По запросу владельца",
    "duplicate": "Дублирующее пространство",
    "contract_ended": "Завершение обслуживания",
    "test_completed": "Завершение тестирования",
}
POLICY_LABELS = {
    "preserve": "Сохранить весь существующий аудит",
    "expire_elapsed": "Удалить только аудит с истёкшим сроком хранения",
}
REVIEW_TTL_SECONDS = 900
MAX_EXPIRE_BATCH = 5000


class OffboardingError(Exception):
    """Bounded public failure, with no database/provider details."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _serializer():
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="workspace-offboarding-v1")


def _ambient_actor(actor_id: int) -> None:
    actor = get_current_user()
    if (
        type(actor_id) is not int
        or actor_id <= 0
        or actor is None
        or actor.id != actor_id
        or not actor.is_superuser
        or not is_bypass()
    ):
        raise OffboardingError(403, "superuser_required", "Удаление доступно только собственнику платформы.")


async def _fresh_actor(session, actor_id: int) -> None:
    allowed = (
        await session.execute(
            select(User.id)
            .join(PlatformRole, PlatformRole.id == User.role_id)
            .where(User.id == actor_id, User.is_active.is_(True), PlatformRole.codename == "superuser")
            .with_for_update(read=True, of=User)
        )
    ).scalar_one_or_none()
    if allowed is None:
        raise OffboardingError(403, "superuser_required", "Права собственника платформы больше недоступны.")


async def _journal_ready(session) -> None:
    try:
        await WorkspaceOffboardingEvent.objects.ensure_available(session=session)
    except RuntimeError:
        raise OffboardingError(
            503, "migration_required", "Журнал удаления ещё не подготовлен. Необходимо применить миграцию 0119."
        ) from None


def _choices(reason_code: str, retention_policy: str) -> None:
    if (
        not isinstance(reason_code, str)
        or not isinstance(retention_policy, str)
        or reason_code not in REASON_LABELS
        or retention_policy not in POLICY_LABELS
    ):
        raise OffboardingError(400, "invalid_policy", "Выберите причину удаления и политику хранения.")


def _version(workspace) -> str:
    return f"{workspace.plan}:{workspace.updated_at.isoformat() if workspace.updated_at else ''}"


async def _summary(session, workspace, retention_policy: str) -> dict:
    now = datetime.now(timezone.utc)
    days = Tenant.PLAN_LIMITS.get(workspace.plan, {}).get("retention_days")
    cutoff = now - timedelta(days=days) if type(days) is int and days > 0 else None
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(ActionAuditEvent).where(ActionAuditEvent.tenant_id == workspace.id)
            )
        ).scalar_one()
    )
    expired = 0
    if cutoff is not None:
        expired = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ActionAuditEvent)
                    .where(ActionAuditEvent.tenant_id == workspace.id, ActionAuditEvent.occurred_at < cutoff)
                )
            ).scalar_one()
        )
    protected = 0
    for table in Base.metadata.tables.values():
        if table is ActionAuditEvent.__table__:
            continue
        for constraint in table.foreign_key_constraints:
            elements = list(constraint.elements)
            if (
                len(elements) == 1
                and elements[0].column.table is Tenant.__table__
                and (constraint.ondelete or "NO ACTION").upper() not in {"CASCADE", "SET NULL"}
            ):
                protected += int(
                    (
                        await session.execute(
                            select(func.count()).select_from(table).where(elements[0].parent == workspace.id)
                        )
                    ).scalar_one()
                )
    blockers = []
    if total - expired:
        blockers.append("Есть аудит, срок хранения которого ещё не истёк.")
    if expired and retention_policy == "preserve":
        blockers.append("Выбрано сохранение всего аудита. Для удаления просроченных записей нужен явный выбор.")
    if expired > MAX_EXPIRE_BATCH:
        blockers.append("Сначала выполните отдельную плановую очистку просроченного аудита.")
    if protected:
        blockers.append("Есть защищённые финансовые или служебные записи: нужна отдельная процедура хранения.")
    return {
        "retained_audit_count": total - expired,
        "expired_audit_count": expired,
        "protected_record_count": protected,
        "retention_days": days,
        "blockers": blockers,
        "can_delete": not blockers,
    }


async def preview_offboarding(
    actor_id: int, workspace_id: int, reason_code: str = "owner_request", retention_policy: str = "preserve"
) -> dict:
    """Read a preview and issue an actor/payload-bound short-lived review."""
    _ambient_actor(actor_id)
    _choices(reason_code, retention_policy)
    if type(workspace_id) is not int or not 0 < workspace_id <= 2147483647:
        raise OffboardingError(400, "invalid_workspace", "Выберите одно рабочее пространство.")
    async with async_session_maker() as session:
        await _fresh_actor(session, actor_id)
        await _journal_ready(session)
        workspace = (await session.execute(select(Tenant).where(Tenant.id == workspace_id))).scalar_one_or_none()
        if workspace is None:
            raise OffboardingError(404, "workspace_missing", "Рабочее пространство не найдено.")
        summary = await _summary(session, workspace, retention_policy)
        token = _serializer().dumps(
            {
                "actor_id": actor_id,
                "workspace_id": workspace.id,
                "reason_code": reason_code,
                "retention_policy": retention_policy,
                "version": _version(workspace),
                "event_id": uuid4().hex,
            }
        )
        return {
            **summary,
            "workspace_id": workspace.id,
            "workspace_name": workspace.name,
            "reason_code": reason_code,
            "retention_policy": retention_policy,
            "review_token": token,
        }


def _read_review(actor_id: int, review_token: str) -> dict:
    if not isinstance(review_token, str) or len(review_token) > 4096:
        raise OffboardingError(
            400, "invalid_review", "Подтверждение недействительно. Повторите предварительный просмотр."
        )
    try:
        payload = _serializer().loads(review_token, max_age=REVIEW_TTL_SECONDS)
    except BadData:
        raise OffboardingError(
            400, "invalid_review", "Подтверждение истекло или недействительно. Повторите просмотр."
        ) from None
    if (
        not isinstance(payload, dict)
        or payload.get("actor_id") != actor_id
        or type(payload.get("workspace_id")) is not int
        or not 0 < payload["workspace_id"] <= 2147483647
        or not isinstance(payload.get("event_id"), str)
        or re.fullmatch(r"[0-9a-f]{32}", payload["event_id"]) is None
        or not isinstance(payload.get("version"), str)
    ):
        raise OffboardingError(400, "invalid_review", "Подтверждение не принадлежит текущему пользователю.")
    _choices(payload.get("reason_code"), payload.get("retention_policy"))
    return payload


def _receipt(event) -> dict:
    return {
        "event_id": event.event_id,
        "workspace_id": event.workspace_id,
        "actor_id": event.actor_id,
        "occurred_at": event.occurred_at.isoformat(),
        "reason_code": event.reason_code,
        "retention_policy": event.retention_policy,
        "expired_audit_count": event.expired_audit_count,
        "outcome": event.outcome,
    }


async def confirm_offboarding(actor_id: int, review_token: str) -> dict:
    """Commit deletion and its success receipt together, or roll both back.

    The workspace FOR UPDATE serializes competitors and conflicts with FK
    inserts. Identical signed review retries return their existing receipt.
    Another operation's deleted workspace is a conflict, never a new success.
    """
    _ambient_actor(actor_id)
    payload = _read_review(actor_id, review_token)
    try:
        async with async_session_maker() as session:
            async with session.begin():
                await _fresh_actor(session, actor_id)
                await _journal_ready(session)
                workspace = (
                    await session.execute(select(Tenant).where(Tenant.id == payload["workspace_id"]).with_for_update())
                ).scalar_one_or_none()
                existing = (
                    await session.execute(
                        select(WorkspaceOffboardingEvent).where(
                            WorkspaceOffboardingEvent.event_id == payload["event_id"]
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    if existing.actor_id != actor_id or existing.workspace_id != payload["workspace_id"]:
                        raise OffboardingError(409, "review_conflict", "Подтверждение не соответствует операции.")
                    return _receipt(existing)
                if workspace is None:
                    raise OffboardingError(409, "workspace_missing", "Пространство уже удалено. Обновите список.")
                if _version(workspace) != payload["version"]:
                    raise OffboardingError(
                        409, "stale_review", "Настройки пространства изменились. Повторите просмотр."
                    )
                summary = await _summary(session, workspace, payload["retention_policy"])
                if summary["blockers"]:
                    raise OffboardingError(409, "retention_blocked", " ".join(summary["blockers"]))
                expired = 0
                if summary["expired_audit_count"]:
                    with tenant_scope(workspace.id):
                        expired = await ActionAuditEvent.objects.delete_expired(
                            session=session, batch_size=MAX_EXPIRE_BATCH
                        )
                event = await WorkspaceOffboardingEvent.objects.record_success(
                    session=session,
                    event_id=payload["event_id"],
                    workspace_id=workspace.id,
                    actor_id=actor_id,
                    reason_code=payload["reason_code"],
                    retention_policy=payload["retention_policy"],
                    expired_audit_count=expired,
                )
                await session.delete(workspace)
                await session.flush()
                receipt = _receipt(event)
            return receipt
    except IntegrityError:
        raise OffboardingError(
            409, "protected_records", "Удаление отменено: связанные защищённые записи должны быть сохранены."
        ) from None
