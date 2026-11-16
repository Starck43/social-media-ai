"""Pure policy only; not authorization, persistence or notification.

The caller must freshly reload and lock the tenant, active member, rights,
document, revision and request in the transaction that records the decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Decision = Literal["approved", "rejected", "revision_requested"]
Status = Literal["pending", "approved", "rejected", "revision_requested", "expired"]
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ApprovalConflict(ValueError):
    """Request is stale, expired, already decided, or inconsistent."""


@dataclass(frozen=True)
class ApprovalSnapshot:
    """Server-owned values read under locks, not caller assertions."""

    revision_id: int
    content_hash: str
    approver_id: int
    status: Status
    expires_at: datetime


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


def check_decision(
    approval: ApprovalSnapshot,
    *,
    actor_id: int,
    current_revision_id: int,
    current_content_hash: str,
    decision: Decision,
    now: datetime,
) -> None:
    """Check assignee, state, deadline, and exact current revision.

    Success is not permission to persist: the service must independently
    revalidate membership and the dedicated approval right. Revision identity
    is checked even if the content hash is unchanged, including metadata edits.
    """
    if (
        type(approval.revision_id) is not int
        or approval.revision_id <= 0
        or type(approval.approver_id) is not int
        or approval.approver_id <= 0
        or type(actor_id) is not int
        or actor_id <= 0
        or type(current_revision_id) is not int
        or current_revision_id <= 0
        or not isinstance(approval.content_hash, str)
        or not _HASH.fullmatch(approval.content_hash)
        or not isinstance(current_content_hash, str)
        or not _HASH.fullmatch(current_content_hash)
        or not _aware(approval.expires_at)
        or not _aware(now)
    ):
        raise ApprovalConflict("invalid_snapshot")
    if decision not in ("approved", "rejected", "revision_requested"):
        raise ApprovalConflict("invalid_decision")
    if approval.status != "pending":
        raise ApprovalConflict("not_pending")
    if approval.approver_id != actor_id:
        raise ApprovalConflict("wrong_approver")
    if approval.revision_id != current_revision_id or approval.content_hash != current_content_hash:
        raise ApprovalConflict("revision_changed")
    if now >= approval.expires_at:
        raise ApprovalConflict("expired")


def _digest(value):
    """Stable intent digest; comments and document content are never logged."""
    import hashlib
    import json

    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _result(row):
    return {
        "id": row.id,
        "document_id": row.document_id,
        "revision_id": row.revision_id,
        "revision_hash": row.revision_hash,
        "requester_user_id": row.requester_user_id,
        "approver_user_id": row.approver_user_id,
        "status": row.status,
        "requested_at": row.requested_at,
        "responded_at": row.responded_at,
        "expires_at": row.expires_at,
    }


async def _member(session, tenant_id, user_id, required):
    """Read and lock live membership, role and the exact persisted role grants."""
    from sqlalchemy import select

    from app.core.access_catalog import workspace_grants
    from app.models import ModelType, Permission, TenantUser, User, WorkspaceRole
    from app.models.tenancy.workspace_role import workspace_role_permission

    user = await session.scalar(
        select(User.id).where(User.id == user_id, User.is_active.is_(True)).with_for_update(read=True)
    )
    membership = await session.scalar(
        select(TenantUser)
        .where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.user_id == user_id,
            TenantUser.channel == "web",
            TenantUser.is_active.is_(True),
        )
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    role = (
        None
        if membership is None
        else await session.scalar(
            select(WorkspaceRole).where(WorkspaceRole.id == membership.role_id).with_for_update(read=True)
        )
    )
    if user is None or role is None:
        raise PermissionError("approval_denied")
    grants = workspace_grants(role.codename)
    for resource, model_name, action in required:
        if (model_name, action) not in grants:
            raise PermissionError("approval_denied")
        # Lock the role-to-permission row as well as membership: revocation
        # cannot slip between authorization and decision commit.
        link = await session.scalar(
            select(workspace_role_permission.c.permission_id)
            .join(Permission, Permission.id == workspace_role_permission.c.permission_id)
            .join(ModelType, ModelType.id == Permission.model_type_id)
            .where(
                workspace_role_permission.c.role_id == role.id,
                ModelType.resource_key == resource,
                Permission.action_type == action,
            )
            .with_for_update(read=True, of=(workspace_role_permission, Permission, ModelType))
        )
        if link is None:
            raise PermissionError("approval_denied")
    return role.codename, grants


async def _identity(session, required_action):
    from sqlalchemy import select

    from app.core.access_catalog import RESOURCES
    from app.core.permissions import get_current_user, has_permission
    from app.core.tenant_context import current_tenant_id, is_bypass
    from app.models import Tenant
    from app.types import ActionType

    tenant_id, actor = current_tenant_id(), get_current_user()
    if is_bypass() or type(tenant_id) is not int or tenant_id <= 0 or actor is None or not actor.is_active:
        raise PermissionError("approval_denied")
    if not await session.scalar(
        select(Tenant.id).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)).with_for_update(read=True)
    ):
        raise PermissionError("approval_denied")
    required = [("collection.source", "source", ActionType.VIEW)]
    if required_action == "decision":
        required.append(("documents.approval", "documentapproval", ActionType.CONFIGURE))
    else:
        required.append(("collection.source", "source", ActionType.UPDATE))
    role, grants = await _member(session, tenant_id, actor.id, required)
    if any(not has_permission(actor, model, action) for _, model, action in required):
        raise PermissionError("approval_denied")
    scopes = tuple(
        f"{r.key}.{action.db_value}"
        for r in RESOURCES
        if r.workspace
        for action in r.actions
        if (r.model_name, action) in grants and has_permission(actor, r.model_name, action)
    )
    return tenant_id, actor.id, role, (*scopes, "public")


async def _notice(session, row, event):
    """Create explicit inbox receipts in the approval's existing transaction."""
    from datetime import timedelta, timezone

    from sqlalchemy import select

    from app.models import TenantUser, User
    from app.services.notifications.service import notify
    from app.types import NotificationType

    names = {
        "requested": ("Approval requested", "A document is awaiting your decision."),
        "decided": ("Approval decision recorded", "A decision was recorded for your document."),
        "expired": ("Approval expired", "A document approval request has expired."),
    }
    requested = (
        [row.approver_user_id]
        if event == "requested"
        else [row.requester_user_id] if event == "decided" else [row.requester_user_id, row.approver_user_id]
    )
    recipients = []
    for user_id in sorted({uid for uid in requested if uid is not None}):
        active = await session.scalar(
            select(User.id)
            .join(TenantUser, TenantUser.user_id == User.id)
            .where(
                User.id == user_id,
                User.is_active.is_(True),
                TenantUser.tenant_id == row.tenant_id,
                TenantUser.channel == "web",
                TenantUser.is_active.is_(True),
                TenantUser.role_id.is_not(None),
            )
            .with_for_update(read=True, of=(User, TenantUser))
        )
        if active is not None:
            recipients.append(user_id)
    title, message = names[event]
    await notify.create(
        title=title,
        message=message,
        ntype=NotificationType.SYSTEM_UPDATE,
        entity_type="document_approval",
        entity_id=row.document_id,
        recipient_user_ids=recipients,
        severity="action_required" if event == "requested" else "info",
        dedup_key=f"document-approval:{row.id}:{event}",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        send_to_messenger=False,
        session=session,
    )


class ApprovalService:
    """Tenant-scoped, transactional approval requests and decisions."""

    async def pending(self, *, boundary="cloud", limit=50, before_id=None, session=None):
        from datetime import datetime, timezone

        from sqlalchemy import select

        from app.models import DocumentApproval, DocumentRevision, KnowledgeDocument
        from app.models.managers.knowledge_manager import BOUNDARIES

        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).pending)(self, boundary=boundary, limit=limit, before_id=before_id)
        if boundary not in BOUNDARIES or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_query")
        if before_id is not None and (type(before_id) is not int or before_id <= 0):
            raise ValueError("invalid_query")
        identity = await _identity(session, "decision")
        query = (
            select(DocumentApproval)
            .join(
                KnowledgeDocument,
                (KnowledgeDocument.id == DocumentApproval.document_id)
                & (KnowledgeDocument.tenant_id == DocumentApproval.tenant_id),
            )
            .join(
                DocumentRevision,
                (DocumentRevision.id == KnowledgeDocument.current_revision_id)
                & (DocumentRevision.document_id == KnowledgeDocument.id)
                & (DocumentRevision.tenant_id == KnowledgeDocument.tenant_id),
            )
            .where(
                DocumentApproval.tenant_id == identity[0],
                DocumentApproval.approver_user_id == identity[1],
                DocumentApproval.status == "pending",
                DocumentApproval.expires_at > datetime.now(timezone.utc),
                DocumentApproval.revision_id == DocumentRevision.id,
                DocumentApproval.revision_hash == DocumentRevision.content_hash,
                DocumentRevision.status == "in_review",
                KnowledgeDocument.acl_scope.in_(identity[3]),
                KnowledgeDocument.data_boundary.in_(BOUNDARIES[boundary]),
            )
        )
        if before_id is not None:
            query = query.where(DocumentApproval.id < before_id)
        rows = (await session.scalars(query.order_by(DocumentApproval.id.desc()).limit(limit + 1))).all()
        return {
            "items": [_result(row) for row in rows[:limit]],
            "limit": limit,
            "next_before_id": rows[limit - 1].id if len(rows) > limit else None,
        }

    async def expire_due(self, *, limit=20, session=None):
        """Expire a bounded set for the current tenant; publish in the same transaction."""
        from datetime import datetime, timezone

        from sqlalchemy import select

        from app.core.tenant_context import current_tenant_id, is_bypass
        from app.models import DocumentApproval, KnowledgeDocument, Tenant

        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).expire_due)(self, limit=limit)
        tenant_id = current_tenant_id()
        if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
            raise PermissionError("approval_denied")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_expiry_limit")
        if not await session.scalar(
            select(Tenant.id).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)).with_for_update(read=True)
        ):
            raise PermissionError("approval_denied")
        now = datetime.now(timezone.utc)
        candidates = (
            await session.execute(
                select(DocumentApproval.id, DocumentApproval.document_id)
                .where(
                    DocumentApproval.tenant_id == tenant_id,
                    DocumentApproval.status == "pending",
                    DocumentApproval.expires_at <= now,
                )
                .order_by(DocumentApproval.document_id, DocumentApproval.id)
                .limit(limit)
            )
        ).all()
        expired = 0
        for approval_id, document_id in candidates:
            doc = await session.scalar(
                select(KnowledgeDocument.id)
                .where(KnowledgeDocument.id == document_id, KnowledgeDocument.tenant_id == tenant_id)
                .with_for_update()
            )
            if doc is None:
                continue
            row = await session.scalar(
                select(DocumentApproval)
                .where(
                    DocumentApproval.id == approval_id,
                    DocumentApproval.tenant_id == tenant_id,
                    DocumentApproval.document_id == document_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is not None and row.status == "pending" and row.expires_at <= datetime.now(timezone.utc):
                row.status = "expired"
                await session.flush()
                await _notice(session, row, "expired")
                expired += 1
        return {"expired": expired}

    async def request(self, document_id, data, *, boundary="cloud", session=None):
        from datetime import datetime, timezone

        from app.services.knowledge.approval_contracts import ApprovalRequest

        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).request)(self, document_id, data, boundary=boundary)
        from sqlalchemy import select

        from app.models import DocumentApproval
        from app.services.knowledge.documents import DocumentConflict, canonical_revision_status, document_service
        from app.types import ActionType

        data = ApprovalRequest.model_validate(data.model_dump() if isinstance(data, ApprovalRequest) else data)
        identity = await _identity(session, "request")
        import hashlib

        from sqlalchemy import text

        key = f"{identity[0]}:{identity[1]}:{data.idempotency_key}".encode("utf-8")
        key_hash = int.from_bytes(hashlib.sha256(key).digest()[:4], "big", signed=True)
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, :key)"), {"namespace": 0x503139, "key": key_hash}
        )
        role, grants = await _member(
            session,
            identity[0],
            data.approver_user_id,
            [
                ("collection.source", "source", ActionType.VIEW),
                ("documents.approval", "documentapproval", ActionType.CONFIGURE),
            ],
        )
        doc = await document_service._doc(session, document_id, identity, boundary, lock=True)
        revision = await document_service._revision(session, doc, lock=True)
        if doc.acl_scope != "public":
            from app.core.access_catalog import resolve_permission_key

            resolved = resolve_permission_key(doc.acl_scope)
            if resolved is None or (resolved[0].model_name, resolved[1]) not in grants:
                raise PermissionError("approval_denied")
        if revision.id != data.expected_revision_id or revision.content_hash != data.expected_hash:
            raise DocumentConflict("document_stale")
        intent = _digest(
            {
                "document_id": doc.id,
                "revision_id": revision.id,
                "revision_hash": revision.content_hash,
                "requester": identity[1],
                "approver": data.approver_user_id,
                "expires_at": data.expires_at.astimezone(timezone.utc).isoformat(),
            }
        )
        existing = await session.scalar(
            select(DocumentApproval)
            .where(
                DocumentApproval.tenant_id == identity[0],
                DocumentApproval.requester_user_id == identity[1],
                DocumentApproval.request_idempotency_key == data.idempotency_key,
            )
            .with_for_update()
        )
        now = datetime.now(timezone.utc)
        if existing is not None:
            if existing.request_hash != intent:
                raise ApprovalConflict("request_key_reused")
            if existing.status == "pending" and existing.expires_at <= now:
                existing.status = "expired"
                await session.flush()
                await _notice(session, existing, "expired")
            return _result(existing)
        if data.expires_at <= now:
            raise ApprovalConflict("invalid_expiry")
        current = await session.scalar(
            select(DocumentApproval)
            .where(
                DocumentApproval.tenant_id == identity[0],
                DocumentApproval.document_id == doc.id,
                DocumentApproval.status == "pending",
            )
            .with_for_update()
        )
        if current is not None:
            if (
                current.expires_at <= now
                or current.revision_id != revision.id
                or current.revision_hash != revision.content_hash
            ):
                current.status = "expired"
                await session.flush()
                await _notice(session, current, "expired")
            else:
                raise ApprovalConflict("request_already_pending")
        status, _ = canonical_revision_status(revision.status)
        if status not in ("draft", "in_review"):
            raise ApprovalConflict("invalid_document_state")
        row = DocumentApproval(
            tenant_id=identity[0],
            document_id=doc.id,
            revision_id=revision.id,
            revision_hash=revision.content_hash,
            requester_user_id=identity[1],
            approver_user_id=data.approver_user_id,
            status="pending",
            request_idempotency_key=data.idempotency_key,
            request_hash=intent,
            requested_at=now,
            expires_at=data.expires_at,
        )
        revision.status = "in_review"
        doc.updated_at = now
        session.add(row)
        await session.flush()
        await _notice(session, row, "requested")
        return _result(row)

    async def decide(self, document_id, approval_id, data, *, boundary="cloud", session=None):
        from datetime import datetime, timezone

        from app.services.knowledge.approval_contracts import ApprovalDecision

        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).decide)(self, document_id, approval_id, data, boundary=boundary)
        from sqlalchemy import select

        from app.models import DocumentApproval
        from app.services.knowledge.documents import DocumentUnavailable, document_service

        data = ApprovalDecision.model_validate(data.model_dump() if isinstance(data, ApprovalDecision) else data)
        if type(approval_id) is not int or approval_id <= 0:
            raise DocumentUnavailable("approval_unavailable")
        identity = await _identity(session, "decision")
        doc = await document_service._doc(session, document_id, identity, boundary, lock=True)
        revision = await document_service._revision(session, doc, lock=True)
        row = await session.scalar(
            select(DocumentApproval)
            .where(
                DocumentApproval.id == approval_id,
                DocumentApproval.tenant_id == identity[0],
                DocumentApproval.document_id == doc.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise DocumentUnavailable("approval_unavailable")
        if row.approver_user_id != identity[1]:
            raise PermissionError("approval_denied")
        if row.revision_id != revision.id or row.revision_hash != revision.content_hash:
            raise ApprovalConflict("revision_changed")
        digest = _digest(
            {"approval_id": row.id, "actor_id": identity[1], "decision": data.decision, "comment": data.comment}
        )
        if row.status != "pending":
            if row.status == data.decision and row.decision_hash == digest:
                return _result(row)
            raise ApprovalConflict("already_decided")
        now = datetime.now(timezone.utc)
        if now >= row.expires_at:
            row.status = "expired"
            await session.flush()
            await _notice(session, row, "expired")
            return _result(row)
        check_decision(
            ApprovalSnapshot(row.revision_id, row.revision_hash, row.approver_user_id, row.status, row.expires_at),
            actor_id=identity[1],
            current_revision_id=revision.id,
            current_content_hash=revision.content_hash,
            decision=data.decision,
            now=now,
        )
        if revision.status != "in_review":
            raise ApprovalConflict("invalid_document_state")
        row.status = data.decision
        row.decision_hash = digest
        row.comment_encrypted = data.comment
        row.responded_at = now
        revision.status = data.decision
        revision.decision_actor_user_id = identity[1]
        revision.approved_by = f"user:{identity[1]}" if data.decision == "approved" else None
        doc.updated_at = now
        await session.flush()
        await _notice(session, row, "decided")
        return _result(row)


approval_service = ApprovalService()
