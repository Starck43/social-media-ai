"""Review local proposals and dispatch linked reviews to their owning ledgers."""

from datetime import datetime, timezone

from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import select

from app.core.config import settings
from app.core.database import with_db_session
from app.models import BusinessCommand, DocumentApproval
from app.models.planner import TaskDecision, TaskDecisionLink
from app.services.commands.approval_inbox import approval_inbox
from app.services.commands.ledger import CommandDenied, CommandNotFound
from app.services.knowledge.approvals import approval_service
from app.services.planner.canonical_tasks import task_service
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable
from app.types import ActionType

PREVIEW_TTL = 900
LEDGERS = {"command": BusinessCommand, "document": DocumentApproval}


def _now():
    return datetime.now(timezone.utc)


def _signer():
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="task-decision-v1")


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000 or "\x00" in value:
        raise ValueError("invalid_decision_reason")
    return value.strip()


def _reference(source, source_id):
    return f"{source}:{source_id}"


def _parse(reference):
    source, _, raw_id = reference.partition(":") if isinstance(reference, str) else ("", "", "")
    if source not in {*LEDGERS, "local"} or not raw_id.isascii() or not raw_id.isdecimal() or len(raw_id) > 10:
        raise PlannerUnavailable("decision_unavailable")
    source_id = int(raw_id)
    if not 0 < source_id <= 2**31 - 1 or raw_id != str(source_id):
        raise PlannerUnavailable("decision_unavailable")
    return source, source_id


def _open(task):
    return task.status in {"todo", "in_progress"}


class TaskDecisionService:
    @with_db_session
    async def propose(self, task_id, *, expected_version, reviewer_id, expires_at, reason, session=None):
        """Record an immutable local proposal for the task revision being reviewed."""
        if type(reviewer_id) is not int or reviewer_id <= 0:
            raise ValueError("invalid_reviewer")
        if not isinstance(expires_at, datetime) or expires_at.utcoffset() is None or expires_at <= _now():
            raise ValueError("invalid_expiry")
        identity = await task_service.action_actor(session, ActionType.UPDATE)
        task = await task_service._task(session, task_id, identity, lock=True)
        task_service._editable(task, expected_version)
        if await task_service._active_assignee(session, task.tenant_id, reviewer_id) is None:
            raise PlannerUnavailable("reviewer_unavailable")
        row = TaskDecision(
            tenant_id=task.tenant_id,
            task_id=task.id,
            task_version=task.version,
            reviewer_id=reviewer_id,
            expires_at=expires_at,
            reason=_text(reason),
        )
        session.add(row)
        await session.flush()
        return _reference("local", row.id)

    @with_db_session
    async def bind_produced_review(self, task_id, *, expected_version, reference, session=None):
        """Internal producer hook. Call only with provenance from the workflow that created the review."""
        source, source_id = _parse(reference)
        model = LEDGERS.get(source)
        if model is None:
            raise ValueError("invalid_review_source")
        identity = await task_service.action_actor(session, ActionType.UPDATE)
        task = await task_service._task(session, task_id, identity, lock=True)
        task_service._editable(task, expected_version)
        linked = await session.scalar(select(model.id).where(model.tenant_id == task.tenant_id, model.id == source_id))
        if linked is None:
            raise PlannerUnavailable("decision_unavailable")
        key = (task.tenant_id, task.id, source, source_id)
        if await session.get(TaskDecisionLink, key) is None:
            session.add(TaskDecisionLink(tenant_id=key[0], task_id=key[1], source=key[2], source_id=key[3]))
            await session.flush()

    async def _local(self, session, task, identity, source_id=None, *, lock=False):
        if not task_service.action_allowed(identity, ActionType.UPDATE):
            return []
        query = select(TaskDecision).where(
            TaskDecision.tenant_id == task.tenant_id,
            TaskDecision.task_id == task.id,
            TaskDecision.reviewer_id == identity[1],
        )
        if source_id is None:
            query = query.where(
                TaskDecision.task_version == task.version,
                TaskDecision.outcome.is_(None),
                TaskDecision.expires_at > _now(),
            )
        else:
            query = query.where(TaskDecision.id == source_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return list(await session.scalars(query.order_by(TaskDecision.id)))

    async def _linked(self, session, task, source, source_id, perms, boundary):
        if await session.get(TaskDecisionLink, (task.tenant_id, task.id, source, source_id)) is None:
            return None
        try:
            if source == "command":
                item = await approval_inbox.get_item(source_id, perms=perms, session=session)
                if item["status"] != "pending" or item["raw_state"] != "previewed" or item["is_stale_payload"]:
                    return None
                choices = [
                    choice
                    for choice, permission in (("accepted", "can_approve"), ("rejected", "can_reject"))
                    if item[permission]
                ]
                if choices:
                    return self._summary(
                        source,
                        source_id,
                        item["approval_expires_at"],
                        item["reason_for_review"],
                        item["payload_hash"],
                        choices,
                        item["authoritative_url"],
                    )
            else:
                pending = await approval_service.pending(
                    approval_id=source_id, boundary=boundary, limit=1, session=session
                )
                if pending["items"]:
                    item = pending["items"][0]
                    return self._summary(
                        source,
                        source_id,
                        item["expires_at"],
                        None,
                        [item["document_id"], item["revision_id"], item["revision_hash"]],
                    )
        except (CommandDenied, CommandNotFound, PermissionError):
            return None
        return None

    @staticmethod
    def _summary(source, source_id, expires_at, reason, target_revision, choices=None, href=None):
        return {
            "reference": _reference(source, source_id),
            "source": source,
            "label": {"local": "Решение по задаче", "command": "Команда", "document": "Документ"}[source],
            "choices": ["accepted", "rejected"] if choices is None else choices,
            "expires_at": expires_at,
            "reason": reason,
            "target_revision": target_revision,
            "href": href,
        }

    @with_db_session
    async def pending(self, task_id, *, perms=None, boundary="cloud", session=None):
        identity = await task_service.action_actor(session, ActionType.VIEW)
        task = await task_service._task(session, task_id, identity)
        if not _open(task):
            return []
        local = await self._local(session, task, identity)
        results = [self._summary("local", row.id, row.expires_at, row.reason, row.task_version) for row in local]
        links = await session.scalars(
            select(TaskDecisionLink)
            .where(TaskDecisionLink.tenant_id == task.tenant_id, TaskDecisionLink.task_id == task.id)
            .order_by(TaskDecisionLink.source, TaskDecisionLink.source_id)
        )
        for link in links:
            item = await self._linked(session, task, link.source, link.source_id, perms, boundary)
            if item is not None:
                results.append(item)
        return results

    @with_db_session
    async def preview(self, task_id, reference, *, outcome, perms=None, boundary="cloud", session=None):
        identity = await task_service.action_actor(session, ActionType.VIEW)
        task = await task_service._task(session, task_id, identity)
        source, source_id = _parse(reference)
        if source == "local":
            rows = await self._local(session, task, identity, source_id)
            row = rows[0] if rows else None
            item = (
                self._summary("local", row.id, row.expires_at, row.reason, row.task_version)
                if row is not None
                and row.outcome is None
                and row.expires_at > _now()
                and row.task_version == task.version
                else None
            )
        else:
            item = await self._linked(session, task, source, source_id, perms, boundary)
        if not _open(task) or item is None or outcome not in item["choices"]:
            raise PlannerUnavailable("decision_unavailable")
        payload = [*identity[:2], task.id, task.version, reference, outcome, item["target_revision"], boundary]
        return {**item, "confirmation": _signer().dumps(payload)}

    @with_db_session
    async def decide(self, task_id, *, confirmation, reason, perms=None, boundary="cloud", session=None):
        reason = _text(reason)
        try:
            tenant_id, actor_id, bound_id, version, reference, outcome, target, bound_boundary = _signer().loads(
                confirmation, max_age=PREVIEW_TTL
            )
        except (BadData, TypeError, ValueError):
            raise PlannerConflict("decision_preview_required") from None
        identity = await task_service.action_actor(session, ActionType.VIEW)
        task = await task_service._task(session, task_id, identity)
        if (tenant_id, actor_id, bound_id, version, bound_boundary) != (
            *identity[:2],
            task.id,
            task.version,
            boundary,
        ) or not _open(task):
            raise PlannerConflict("decision_changed")
        source, source_id = _parse(reference)
        if source == "local":
            rows = await self._local(session, task, identity, source_id, lock=True)
            if not rows:
                raise PlannerUnavailable("decision_unavailable")
            row = rows[0]
            if row.outcome is not None:
                if row.outcome != outcome or row.outcome_reason != reason:
                    raise PlannerConflict("already_decided")
            elif row.task_version == target == task.version and row.expires_at > _now():
                row.outcome, row.outcome_reason, row.decided_at = outcome, reason, _now()
                await session.flush()
            else:
                raise PlannerConflict("decision_changed")
            return {"reference": reference, "outcome": row.outcome, "decided_at": row.decided_at}
        if source == "command":
            command = await approval_inbox._lock_command_row(session, task.tenant_id, source_id)
            if command is None or command.state != "previewed":
                raise PlannerConflict("decision_changed")
        item = await self._linked(session, task, source, source_id, perms, boundary)
        if item is None or target != item["target_revision"] or outcome not in item["choices"]:
            raise PlannerConflict("decision_changed")
        if source == "document":
            result = await approval_service.decide(
                target[0],
                source_id,
                {"decision": "approved" if outcome == "accepted" else "rejected", "comment": reason},
                boundary=boundary,
                session=session,
            )
        elif outcome == "accepted":
            result = await approval_inbox.approve_item(
                source_id, expected_payload_hash=target, perms=perms, session=session
            )
        else:
            result = await approval_inbox.reject_item(
                source_id,
                expected_payload_hash=target,
                reason=reason,
                perms=perms,
                session=session,
            )
        return {"reference": reference, "outcome": result["status"]}


task_decision_service = TaskDecisionService()
