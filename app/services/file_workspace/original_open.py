"""Internal two-phase original-open orchestration; deliberately not UI-wired.

No default resolver/client, public route, feature activation or stored link.
P8-T34 owns real provider authorization/network/native-target implementation.
"""

from __future__ import annotations

import asyncio

from app.integrations.file_workspace.original_open import (
    RESOLVE_TIMEOUT,
    NativeLinkPolicy,
    OriginalOpenUnavailable,
    OriginalResolver,
    ResolvedOriginal,
    validate_target,
)
from app.models.managers.file_catalog_manager import FileCatalogManager


async def resolve_original(
    *, tenant_id: int, actor_id: int, connection_id: int, object_id: int, resolver: OriginalResolver | None = None
) -> ResolvedOriginal:
    """Resolve only a current catalog file under freshly checked LIST and READ.

    The injected resolver/policy is application-controlled, never request input.
    No transaction/connection lock is held over provider work: current authority
    and exact catalog context are rechecked after it. Cancellation propagates.
    Provider side effects and an already delivered URL cannot be revoked here.
    """
    if resolver is None:
        raise OriginalOpenUnavailable()
    try:
        policy = resolver.link_policy
        if type(policy) is not NativeLinkPolicy:
            raise OriginalOpenUnavailable()
        policy.validate()
    except Exception:
        raise OriginalOpenUnavailable() from None
    manager = FileCatalogManager()
    context = await manager.prepare_original(
        tenant_id=tenant_id, actor_id=actor_id, connection_id=connection_id, object_id=object_id
    )
    try:
        async with asyncio.timeout(RESOLVE_TIMEOUT):
            target = await resolver.resolve(context)
    except Exception:
        raise OriginalOpenUnavailable() from None
    # Revalidate before release, including any time spent waiting for DB locks.
    await manager.check_original(
        tenant_id=tenant_id, actor_id=actor_id, connection_id=connection_id, object_id=object_id, context=context
    )
    return validate_target(context, target, policy)
