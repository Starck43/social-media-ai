"""Operator-approved, read-only and bounded source identity inventory.

Prints IDs and coarse metadata states only, never source names, params, secret
columns, credential labels or provider responses. It cannot approve or perform a
mapping, and it does not claim a credential is usable or a provider grants it.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import sqlalchemy as sa


class InventoryError(RuntimeError):
    """A bounded, secret-free inventory failure."""


def _owner(value):
    """Conservative ID parsing: malformed values remain unresolved, never guessed."""
    if type(value) is int and 0 < value <= 2147483647:
        return value
    if type(value) is str and len(value) <= 10 and value.isascii() and value.isdecimal():
        parsed = int(value)
        return parsed if 0 < parsed <= 2147483647 else None
    return None


def inventory(conn, *, schema: str, tenant_id: int, after_id: int = 0, limit: int = 100) -> dict:
    """Return one page from a caller-owned, PostgreSQL read-only transaction."""
    if type(tenant_id) is not int or tenant_id <= 0 or type(after_id) is not int or after_id < 0:
        raise InventoryError("Invalid workspace or cursor")
    if type(limit) is not int or not 1 <= limit <= 200 or not schema:
        raise InventoryError("Invalid page size or schema")
    if not conn.in_transaction() or conn.exec_driver_sql("SHOW transaction_read_only").scalar_one() != "on":
        raise InventoryError("A read-only transaction is required")
    if conn.engine.echo or conn.engine.logger.isEnabledFor(logging.INFO):
        raise InventoryError("Disable SQL and result logging before inventory")
    quote = conn.dialect.identifier_preparer.quote_schema(schema)
    rows = (
        conn.execute(
            sa.text(
                f"SELECT s.id, p.platform_type::text AS platform, s.is_active, s.owner_user_id, "
                "CASE WHEN CAST(s.params AS jsonb)->'token_owner' IS NULL "
                "OR CAST(s.params AS jsonb)->'token_owner' = CAST('null' AS jsonb) THEN NULL "
                "WHEN jsonb_typeof(CAST(s.params AS jsonb)->'token_owner') IN ('string','number') "
                "AND length(CAST(s.params AS jsonb)->>'token_owner') <= 10 "
                "THEN CAST(s.params AS jsonb)->>'token_owner' ELSE '<invalid>' END AS selected_owner, "
                "CASE WHEN CAST(s.params AS jsonb)->>'mode' IN ('auto','api','user') "
                "THEN CAST(s.params AS jsonb)->>'mode' "
                "WHEN CAST(s.params AS jsonb)->>'mode' IS NULL THEN 'auto' ELSE 'unknown' END AS mode "
                f"FROM {quote}.sources s JOIN {quote}.platforms p ON p.id=s.platform_id "
                "WHERE s.tenant_id=:tenant AND s.id>:cursor AND p.platform_type::text IN ('vk','telegram') "
                "ORDER BY s.id LIMIT :page_size"
            ),
            {"tenant": tenant_id, "cursor": after_id, "page_size": limit + 1},
        )
        .mappings()
        .all()
    )
    more = len(rows) > limit
    rows = rows[:limit]
    candidates = []
    for row in rows:
        explicit = row["selected_owner"] is not None
        raw = row["selected_owner"] if explicit else row["owner_user_id"]
        candidate = _owner(raw)
        candidates.append((row, explicit, raw is not None, candidate))
    owner_ids = sorted({candidate for _, _, _, candidate in candidates if candidate is not None})
    members = {}
    kinds = {}
    if owner_ids:
        memberships = conn.execute(
            sa.text(
                f"SELECT tu.user_id, u.is_active AS user_active FROM {quote}.tenant_users tu "
                f"JOIN {quote}.users u ON u.id=tu.user_id "
                "WHERE tu.tenant_id=:tenant AND tu.channel='web' AND tu.is_active IS TRUE "
                "AND tu.user_id = ANY(:owners)"
            ),
            {"tenant": tenant_id, "owners": owner_ids},
        ).mappings()
        members = {row["user_id"]: row["user_active"] is True for row in memberships}
        eligible = sorted(user_id for user_id, active in members.items() if active)
        metadata = conn.execute(
            sa.text(
                f"SELECT DISTINCT user_id, platform, kind FROM {quote}.user_credentials "
                "WHERE user_id = ANY(:owners) AND is_active IS TRUE AND "
                "((platform='vk' AND kind='user_token') OR "
                "(platform='telegram' AND kind IN ('api_id','api_hash','session')))"
            ),
            {"owners": eligible},
        ).mappings()
        for row in metadata:
            kinds.setdefault((row["user_id"], row["platform"]), set()).add(row["kind"])
    result = []
    for row, explicit, supplied, candidate in candidates:
        if not supplied:
            status = "ownerless"
        elif candidate is None:
            status = "invalid_or_ambiguous"
        elif candidate not in members:
            status = "not_active_workspace_member"
        elif not members[candidate]:
            status = "account_inactive"
        else:
            status = "active_member"
        material = None
        if status == "active_member":
            present = kinds.get((candidate, row["platform"]), set())
            expected = {"user_token"} if row["platform"] == "vk" else {"api_id", "api_hash", "session"}
            material = "metadata_complete_unverified" if expected <= present else "partial_or_missing"
        mode = row["mode"] or "auto"
        if mode not in ("auto", "api", "user"):
            mode = "unknown"
        result.append(
            {
                "source_id": row["id"],
                "platform": row["platform"],
                "source_active": row["is_active"] is True,
                "mode": mode,
                "owner_origin": "explicit" if explicit else "recorded" if supplied else "none",
                "owner_user_id": candidate if status == "active_member" else None,
                "owner_status": status,
                "personal_material": material,
            }
        )
    return {"tenant_id": tenant_id, "sources": result, "next_after_id": rows[-1]["id"] if more else None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only, bounded source identity inventory")
    parser.add_argument("--target-approved", action="store_true", help="Confirm this workspace target is authorized")
    parser.add_argument("--tenant-id", type=int, required=True)
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    if not args.target_approved:
        parser.error("--target-approved is required")
    if args.tenant_id <= 0 or args.after_id < 0 or not 1 <= args.limit <= 200:
        parser.error("invalid workspace, cursor or page size")
    from app.core.config import settings
    from app.core.database import engine

    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            report = inventory(
                conn, schema=settings.DB_SCHEMA, tenant_id=args.tenant_id, after_id=args.after_id, limit=args.limit
            )
    except Exception:
        # Driver errors may contain connection details or raw parameters.
        print("Source inventory did not complete; verify authorization and database access.", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
