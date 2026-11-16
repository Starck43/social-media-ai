"""Owner-bound download authorization; no provider I/O while ORM locks are held."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.integrations.file_workspace.original_open import OriginalOpenContext, context_live
from app.integrations.file_workspace.policy import FileOperation
from app.models import FileCatalogObject, WorkspaceConnectionSecret
from app.models.file_workspace.private_insights import FileInsightMount
from app.models.managers.file_catalog_manager import _key, _owned, _policy_key
from app.models.managers.file_insight_manager import POLICY_REVISION
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.utils.crypto import decrypt_secret


@dataclass(frozen=True, slots=True, repr=False)
class AuthorizedDownload:
    context: OriginalOpenContext
    name: str
    token: str
    processing_revision: str | None


class FileContentManager:
    def __init__(self, *, enabled=False):
        self.enabled = enabled is True

    async def authorize(self, *, tenant_id, actor_id, connection_id, object_id, processing=False, previous=None):
        try:
            if (
                not self.enabled
                or type(processing) is not bool
                or type(object_id) is not int
                or not 1 <= object_id <= 2147483647
            ):
                raise ValueError
            if previous is not None and (
                type(previous) is not AuthorizedDownload or not context_live(previous.context)
            ):
                raise ValueError
            async with _owned(tenant_id, actor_id, connection_id) as (session, connection, policy, root_key):
                row = await session.scalar(
                    select(FileCatalogObject).where(
                        FileCatalogObject.id == object_id,
                        FileCatalogObject.tenant_id == tenant_id,
                        FileCatalogObject.connection_id == connection_id,
                        FileCatalogObject.root_key == root_key,
                        FileCatalogObject.kind == "file",
                    )
                )
                if row is None or not all(
                    policy.permits(row.path, op) for op in (FileOperation.LIST, FileOperation.READ)
                ):
                    raise ValueError
                secret = await session.get(WorkspaceConnectionSecret, connection_id)
                if secret is None or not secret.secret_encrypted:
                    raise ValueError
                context = OriginalOpenContext(
                    tenant_id,
                    actor_id,
                    connection_id,
                    object_id,
                    row.path,
                    root_key,
                    _policy_key(connection.config),
                    _key(json.dumps([connection.account_key, secret.secret_encrypted], ensure_ascii=True)),
                    row.observation_number,
                    previous.context.issued_at if previous else datetime.now(timezone.utc),
                )
                revision = None
                if processing:
                    mount = await session.scalar(
                        select(FileInsightMount)
                        .where(
                            FileInsightMount.tenant_id == tenant_id,
                            FileInsightMount.connection_id == connection_id,
                            FileInsightMount.owner_id == actor_id,
                        )
                        .with_for_update(read=True)
                    )
                    if (
                        mount is None
                        or mount.processing_allowed is not True
                        or mount.policy_revision != POLICY_REVISION
                    ):
                        raise ValueError
                    revision = mount.consent_revision
                if previous is not None and (
                    previous.context != context or previous.processing_revision != revision or previous.name != row.name
                ):
                    raise ValueError
                return AuthorizedDownload(context, row.name, decrypt_secret(secret.secret_encrypted), revision)
        except Exception:
            pass
        raise ConnectionDenied()
