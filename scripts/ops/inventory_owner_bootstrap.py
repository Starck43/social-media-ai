"""Explicitly authorized, bounded, read-only messenger bootstrap inventory.

No external user IDs, chat IDs, credentials, or allowlist values are output.
The configured allowlist belongs only to this process, not all deployed workers.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import sqlalchemy as sa


class InventoryError(RuntimeError):
    """A non-sensitive inventory failure."""


def inventory(conn, *, schema, tenant_id, bootstrap_slug, allowed, member_after=0, binding_after=0, limit=100):
    if type(tenant_id) is not int or tenant_id <= 0 or not schema or not bootstrap_slug:
        raise InventoryError("Invalid target")
    if any(type(n) is not int or n < 0 for n in (member_after, binding_after)) or not 1 <= limit <= 200:
        raise InventoryError("Invalid page")
    if not conn.in_transaction() or conn.exec_driver_sql("SHOW transaction_read_only").scalar_one() != "on":
        raise InventoryError("A read-only transaction is required")
    if conn.engine.echo or conn.engine.logger.isEnabledFor(logging.INFO):
        raise InventoryError("Disable SQL and result logging before inventory")
    q = conn.dialect.identifier_preparer.quote_schema(schema)
    target = conn.execute(
        sa.text(f"SELECT id FROM {q}.tenants WHERE id=:tenant AND slug=:slug"),
        {"tenant": tenant_id, "slug": bootstrap_slug},
    ).scalar_one_or_none()
    if target is None:
        raise InventoryError("The target is not this instance's configured bootstrap workspace")
    sets = {channel: {str(value) for value in allowed.get(channel, ())} for channel in ("telegram", "max")}
    if any(len(ids) > 1000 for ids in sets.values()):
        raise InventoryError("Allowlist exceeds the inventory bound")
    members = (
        conn.execute(
            sa.text(
                f"SELECT m.id, m.channel, m.external_user_id, m.is_active, r.codename AS role, "
                f"m.user_id IS NOT NULL AS linked, u.is_active AS user_active FROM {q}.tenant_users m "
                f"LEFT JOIN {q}.workspace_roles r ON r.id=m.role_id LEFT JOIN {q}.users u ON u.id=m.user_id "
                "WHERE m.tenant_id=:tenant AND m.channel IN ('telegram','max') AND m.id>:cursor "
                "ORDER BY m.id LIMIT :page_size"
            ),
            {"tenant": tenant_id, "cursor": member_after, "page_size": limit + 1},
        )
        .mappings()
        .all()
    )
    member_more = len(members) > limit
    members = members[:limit]
    binding_rows = (
        conn.execute(
            sa.text(
                f"SELECT id, channel, kind, is_active, is_digest_target FROM {q}.tenant_channels "
                "WHERE tenant_id=:tenant AND channel IN ('telegram','max') AND id>:cursor "
                "ORDER BY id LIMIT :page_size"
            ),
            {"tenant": tenant_id, "cursor": binding_after, "page_size": limit + 1},
        )
        .mappings()
        .all()
    )
    binding_more = len(binding_rows) > limit
    binding_rows = binding_rows[:limit]
    configured = {}
    for channel, ids in sets.items():
        # Bind allowlist values, never print them. Server-side SQL logs still need
        # operator review. Counts include inactive and non-owner memberships.
        matched = conn.execute(
            sa.text(
                f"SELECT count(DISTINCT external_user_id) FROM {q}.tenant_users "
                "WHERE tenant_id=:tenant AND channel=:channel AND external_user_id = ANY(:ids)"
            ),
            {"tenant": tenant_id, "channel": channel, "ids": sorted(ids)},
        ).scalar_one()
        configured[channel] = {"configured_count": len(ids), "without_membership_count": len(ids) - matched}
    roles = {"owner", "admin", "manager", "analyst", "viewer"}
    return {
        "tenant_id": tenant_id,
        "instance_allowlist": configured,
        "members": [
            {
                "membership_id": row["id"],
                "channel": row["channel"],
                "active": row["is_active"] is True,
                "role": row["role"] if row["role"] in roles else "unresolved_or_other",
                "linked_user": (
                    "active"
                    if row["linked"] and row["user_active"] is True
                    else "inactive_or_missing" if row["linked"] else "not_linked"
                ),
                "allowlisted_on_this_instance": row["external_user_id"] in sets[row["channel"]],
            }
            for row in members
        ],
        "bindings": [
            {
                "binding_id": row["id"],
                "channel": row["channel"],
                "active": row["is_active"] is True,
                "kind": row["kind"] if row["kind"] in ("private", "channel", "group") else "other",
                "digest_target": row["is_digest_target"] is True,
            }
            for row in binding_rows
        ],
        "next_member_after": members[-1]["id"] if member_more else None,
        "next_binding_after": binding_rows[-1]["id"] if binding_more else None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only messenger bootstrap inventory")
    parser.add_argument("--target-approved", action="store_true")
    parser.add_argument("--tenant-id", type=int, required=True)
    parser.add_argument("--member-after", type=int, default=0)
    parser.add_argument("--binding-after", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    if (
        not args.target_approved
        or args.tenant_id <= 0
        or min(args.member_after, args.binding_after) < 0
        or not 1 <= args.limit <= 200
    ):
        parser.error("explicit target approval and valid workspace/page arguments are required")
    from app.core.config import settings
    from app.core.database import engine

    try:
        allowed = {"telegram": settings.telegram_owner_ids(), "max": settings.max_owner_ids()}
        with engine.begin() as conn:
            conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            report = inventory(
                conn,
                schema=settings.DB_SCHEMA,
                tenant_id=args.tenant_id,
                bootstrap_slug=settings.DEFAULT_TENANT_SLUG.strip().lower(),
                allowed=allowed,
                member_after=args.member_after,
                binding_after=args.binding_after,
                limit=args.limit,
            )
    except Exception:
        print(
            "Bootstrap inventory did not complete; verify authorization, configuration and database access.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
