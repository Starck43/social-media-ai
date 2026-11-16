"""Tools: read and preview document version history (P19-T08).

The chat surface is read-only by contract: it lists revisions, diffs two
revisions and previews a restore. It never writes a revision — the approval
stays in the web decision surface, and a restore through the chat needs the
operator CLI or the web form.

Every handler calls `app.services.knowledge.versions` under the caller's own
permission scope, so the workspace membership check, ACL scope and data
boundary are the same ones `/api/v1/documents` applies. A caller that may not
read the document in the current workspace gets `document_unavailable`; one
without `source.view` is refused before the handler runs, by the registry gate
(`required_permission`).
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from app.agent.tools import tool

# Chat surfaces cannot approve a revision; the web decision surface owns it.
APPROVAL_SURFACE = "/app/documents/manage/{document_id}?boundary=local_only"

DOCUMENT_ID_SCHEMA = {"type": "integer", "minimum": 1, "description": "ID документа (никогда не ID ревизии)"}
REVISION_ID_SCHEMA = {"type": "integer", "minimum": 1, "description": "ID ревизии из documents_history"}


def document_approval_link(document_id: int) -> str:
    return APPROVAL_SURFACE.format(document_id=document_id)


async def _redacted(document_id: int, operation):
    """Run a version-service read; refusals become bounded error dicts.

    Only the service's own refusal and conflict types are translated; an
    unexpected failure still propagates so dispatch reports `tool_failed`
    rather than inventing a message.
    """
    from app.services.knowledge.documents import DocumentConflict, DocumentUnavailable

    try:
        return await operation()
    except PermissionError:
        return {"error": "Недостаточно прав для доступа к документу", "code": "permission_denied"}
    except DocumentUnavailable:
        return {
            "error": f"Документ или ревизия {document_id} недоступны в этом workspace",
            "code": "document_unavailable",
        }
    except DocumentConflict:
        return {"error": "Документ изменился; история неактуальна", "code": "document_conflict"}
    except ValueError:
        return {"error": "Некорректные параметры запроса истории", "code": "invalid_request"}


@tool(
    name="documents_history",
    description=(
        "История ревизий документа: id, версия, хэш, автор, дата и описание изменения (новые первыми). "
        "Только чтение; восстановление ревизии доступно на веб-странице документа."
    ),
    parameters={
        "type": "object",
        "properties": {
            "document_id": DOCUMENT_ID_SCHEMA,
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        },
        "required": ["document_id"],
    },
    required_permission="collection.source.view",
)
async def documents_history(document_id: int, limit: int = 20) -> dict[str, Any]:
    from app.services.knowledge.documents import document_service
    from app.services.knowledge.versions import version_service

    async def _read():
        # Same two calls the web page makes: the current revision identity and
        # the revision page, both scoped by the service's own access check.
        current = await document_service.get(document_id, boundary="local_only")
        page = await version_service.list(document_id, boundary="local_only", limit=limit)
        return {
            "document_id": document_id,
            "current_revision_id": current.current_revision_id,
            "version": current.version,
            "status": current.status,
            "approval_invalidated": current.approval_invalidated,
            "items": [
                {
                    "revision_id": revision.id,
                    "version": revision.version,
                    "content_hash": revision.content_hash,
                    "author": revision.author,
                    "created_at": revision.created_at.isoformat(),
                    "change_description": revision.change_description,
                }
                for revision in page["items"]
            ],
            "limit": page["limit"],
            "next_before_id": page["next_before_id"],
            "approval_link": document_approval_link(document_id),
        }

    return await _redacted(document_id, _read)


@tool(
    name="documents_diff",
    description=(
        "Сравнить две ревизии документа построчно или по словам; возвращает части diff с before/after. "
        "Только чтение, без HTML и без изменения документа."
    ),
    parameters={
        "type": "object",
        "properties": {
            "document_id": DOCUMENT_ID_SCHEMA,
            "from_revision_id": REVISION_ID_SCHEMA,
            "to_revision_id": REVISION_ID_SCHEMA,
            "granularity": {"type": "string", "enum": ["line", "word"], "default": "line"},
        },
        "required": ["document_id", "from_revision_id", "to_revision_id"],
    },
    required_permission="collection.source.view",
)
async def documents_diff(
    document_id: int,
    from_revision_id: int,
    to_revision_id: int,
    granularity: str = "line",
) -> dict[str, Any]:
    from app.services.knowledge.versions import version_service

    async def _diff():
        result = await version_service.diff(
            document_id, from_revision_id, to_revision_id, granularity=granularity, boundary="local_only"
        )
        return {
            "document_id": document_id,
            "from_revision_id": result.from_revision_id,
            "to_revision_id": result.to_revision_id,
            "granularity": result.granularity,
            "parts": [
                {"operation": part.operation, "before": part.before, "after": part.after} for part in result.parts
            ],
        }

    return await _redacted(document_id, _diff)


@tool(
    name="documents_restore_preview",
    description=(
        "Показать, какая ревизия была бы создана при восстановлении, без записи и без подтверждения. "
        "Восстановление выполняется на веб-странице документа оператором с правом изменения."
    ),
    parameters={
        "type": "object",
        "properties": {
            "document_id": DOCUMENT_ID_SCHEMA,
            "revision_id": REVISION_ID_SCHEMA,
        },
        "required": ["document_id", "revision_id"],
    },
    required_permission="collection.source.view",
)
async def documents_restore_preview(document_id: int, revision_id: int) -> dict[str, Any]:
    from app.services.knowledge.documents import DocumentUnavailable, document_service
    from app.services.knowledge.versions import RestoreRequest, version_service

    async def _preview():
        current = await document_service.get(document_id, boundary="local_only")
        page = await version_service.list(document_id, boundary="local_only", limit=100)
        source = next((revision for revision in page["items"] if revision.id == revision_id), None)
        if source is None:
            raise DocumentUnavailable("document_unavailable")
        change_description = f"Restored revision {source.version}"
        # The previewed request must be valid for the real write path too, so a
        # stale or malformed current revision is reported here rather than later.
        RestoreRequest.model_validate(
            {
                "expected_revision_id": current.current_revision_id,
                "expected_updated_at": current.updated_at.isoformat(),
                "change_description": change_description,
            }
        )
        # The service exposes no revision-body reader other than diff; a
        # self-diff returns the stored revision whole.
        body = (await version_service.diff(document_id, source.id, source.id, boundary="local_only")).parts[0].before
        return {
            "document_id": document_id,
            "dry_run": True,
            "current_version": current.version,
            "current_revision_id": current.current_revision_id,
            "source_revision_id": source.id,
            "source_version": source.version,
            "would_create_version": current.version + 1,
            "would_create_hash": source.content_hash,
            "would_create_status": "draft_invalidated",
            "change_description": change_description,
            "body_preview": body[:2000],
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "body_base64": base64.b64encode(body.encode("utf-8")).decode("ascii"),
            "approval_link": document_approval_link(document_id),
            "next_step": "Решение и восстановление — на веб-странице документа; из чата запись не выполняется.",
        }

    return await _redacted(document_id, _preview)
