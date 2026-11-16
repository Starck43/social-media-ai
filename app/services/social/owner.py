"""Pick which web user's personal (L2) token a source collects with.

A source is *workspace*-scoped, but the L2 secret it may need (VK `user_token`,
Telegram MTProto `session`) belongs to a *person* and lives in `user_credentials`
keyed by `users.id`. The owner is chosen, in order:

1. `Source.params["token_owner"]` — an explicit `users.id`, accepted only when
   that user is an active web member of the source's workspace (`tenant_users`).
   A foreign or stale id is logged and ignored, never used.
2. `Source.owner_user_id` — the member who added the source, also re-validated
   against active membership on every run.

There is deliberately **no third step**. A workspace owner does not lend their
personal account to a member's source: the owner provides the platform tooling,
each member authorises their own account. A source with neither owner collects
through the deployment credential only (L1), and the run says so.

Returns None when the source has no usable personal owner, so collection stays on
L1 instead of borrowing somebody else's token. This is the "call site" that
enforces the personal-vault access rule (a workspace may only use a member's
token); the vault itself stays unscoped.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def resolve_source_owner(source: Any) -> Optional[int]:
    """The `users.id` whose personal credentials a source may collect with."""
    tenant_id = getattr(source, "tenant_id", None)
    if tenant_id is None:
        return None

    explicit = (getattr(source, "params", None) or {}).get("token_owner")
    if explicit is not None:
        try:
            candidate = int(explicit)
        except (TypeError, ValueError):
            logger.warning("Source %s has a non-numeric token_owner %r - ignored", getattr(source, "id", "?"), explicit)
            return None
        return await _active_member_or_none(source, candidate=candidate, tenant_id=tenant_id)

    recorded = getattr(source, "owner_user_id", None)
    if recorded is not None:
        return await _active_member_or_none(source, candidate=recorded, tenant_id=tenant_id)

    # No personal owner: the source collects with the deployment credential only.
    # Never substitute the workspace owner here — that silently lent one person's
    # account to everybody's sources, which is the defect P12-T16 removes.
    return None


async def _active_member_or_none(source: Any, *, candidate: int, tenant_id: int) -> Optional[int]:
    """`candidate` when it is still an active web member of `tenant_id`, else None.

    Re-checked on every run, not just when the source was saved: a member who was
    removed or deactivated must lose access to that source's collection at once.
    """
    from app.models.managers.tenant_manager import tenant_users

    members = await tenant_users.web_memberships_for_tenant(tenant_id)
    if any(member.user_id == candidate for member in members):
        return candidate
    logger.warning(
        "Source %s owner %s is not an active member of workspace %s - ignored (service credential only)",
        getattr(source, "id", "?"),
        candidate,
        tenant_id,
    )
    return None
