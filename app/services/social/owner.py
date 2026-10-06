"""Pick which web user's personal (L2) token a source collects with.

A source is *workspace*-scoped, but the L2 secret it may need (VK `user_token`,
Telegram MTProto `session`) belongs to a *person* and lives in `user_credentials`
keyed by `users.id`. The owner is chosen, in order:

1. `Source.params["token_owner"]` — an explicit `users.id`, accepted only when
   that user is an active web member of the source's workspace (`tenant_users`).
   A foreign or stale id is logged and ignored, never used.
2. otherwise the workspace owner — the newest active web member.

Returns None when the workspace has no web member, so resolution falls back to
the environment exactly as before. This is the "call site" that enforces the
personal-vault access rule (a workspace may only use a member's token); the
vault itself stays unscoped.
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

    from app.models.managers.tenant_manager import tenant_users

    members = sorted(
        await tenant_users.web_memberships_for_tenant(tenant_id),
        key=lambda member: member.id,
    )
    if not members:
        return None

    explicit = (getattr(source, "params", None) or {}).get("token_owner")
    if explicit is not None:
        try:
            user_id = int(explicit)
        except (TypeError, ValueError):
            logger.warning("Source %s has a non-numeric token_owner %r - ignored", getattr(source, "id", "?"), explicit)
            return None
        if user_id not in {member.user_id for member in members}:
            logger.warning(
                "Source %s token_owner %s is not an active member of workspace %s - ignored",
                getattr(source, "id", "?"),
                user_id,
                tenant_id,
            )
            return None
        return user_id

    owners = [member for member in members if member.is_owner] or members
    return owners[-1].user_id
