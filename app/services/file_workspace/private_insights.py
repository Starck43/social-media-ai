"""Default-off internal read -> receipt -> candidates -> retention workflow.

No public route, provider/model default, scheduler or generic DTO-to-storage API.
Trusted app adapters only. The supplied manager must be explicitly enabled after
migration/local validation; real Yandex version authority remains unavailable.
"""

from __future__ import annotations

import asyncio

from app.integrations.file_workspace.content_read import READ_TIMEOUT, PlainTextReadUnavailable
from app.models.managers.file_insight_manager import PrivateInsightUnavailable
from app.services.file_workspace.plain_text_read import _read
from app.services.file_workspace.private_admission import prepare_private_payload


class OwnedContentAuthority:
    def __init__(self, manager):
        self.manager = manager

    async def authorize(self, request):
        return await self.manager.read_context(request)


class OwnedAdmissionAuthority:
    def __init__(self, manager):
        self.manager = manager

    async def authorize(self, request):
        return await self.manager.admission_state(request)


class _CheckedVersion:
    def __init__(self, delegate):
        self.delegate = delegate
        self.assertion = None

    async def verify(self, source, receipt):
        self.assertion = await self.delegate.verify(source, receipt)
        return self.assertion


class PrivateInsightWorkflow:
    def __init__(self, manager):
        self.manager = manager

    async def read(
        self, *, tenant_id, actor_id, connection_id, object_id, reader, version_semantics, provider_object_id=None
    ):
        """Allocate durable original capture BEFORE reader; never redate a save.

        Reader/semantics/object identity are reviewed application dependencies,
        not browser assertions. Unsupported version means no retained receipt.
        Failed attempts stay bounded and expire; no automatic retries.
        """
        try:
            if reader is None:
                raise ValueError
            request = await self.manager.begin_read(
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                object_id=object_id,
            )
            authority = OwnedContentAuthority(self.manager)
            deadline = asyncio.get_running_loop().time() + READ_TIMEOUT
            async with asyncio.timeout(READ_TIMEOUT):
                source = await _read(request, authority, reader, deadline)
            receipt = await self.manager.complete_read(
                source, version_semantics=version_semantics, provider_object_id=provider_object_id
            )
            return source, receipt
        except Exception:
            pass
        raise PlainTextReadUnavailable()

    async def save(self, source, batch, receipt, *, versions):
        """Revalidate outside locks, then manager performs final atomic admission.

        A fresh receipt and application-owned result, not public JSON. Preview is
        deliberately not returned: only the manager's committed result is released.
        Cancellation propagates. A cancelled commit may have completed server-side;
        retry the same attempt, never allocate another attempt blindly.
        """
        try:
            if versions is None:
                raise ValueError
            checked = _CheckedVersion(versions)
            await prepare_private_payload(
                source,
                batch,
                receipt,
                authority=OwnedAdmissionAuthority(self.manager),
                versions=checked,
            )
            return await self.manager.save(source, batch, receipt, checked.assertion)
        except Exception:
            pass
        raise PrivateInsightUnavailable()
