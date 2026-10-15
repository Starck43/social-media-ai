"""Read-only summary of rows affected by access migration 0096.

The report emits counts, role codenames and model table names only. It never
prints account identifiers, credentials, invitations or memory contents.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter

from sqlalchemy import func, literal, literal_column

from app.core.tenant_context import tenant_scope
from app.models import ModelType, Role, TenantInvite, TenantUser, User

RESOURCE_KEYS = {
    "users": "identity.user",
    "roles": "identity.platform_role",
    "permissions": "identity.permission",
    "model_types": "identity.model_type",
    "user_credentials": "identity.credential",
    "tenants": "tenancy.workspace",
    "tenant_users": "tenancy.membership",
    "workspace_roles": "tenancy.workspace_role",
    "tenant_invites": "tenancy.invite",
    "tenant_channels": "tenancy.channel",
    "sources": "collection.source",
    "platforms": "collection.platform",
    "collected_items": "collection.collected_item",
    "agent_scenarios": "analysis.scenario",
    "ai_analytics": "analysis.result",
    "llm_providers": "llm.provider",
    "llm_models": "llm.model",
    "llm_attempts": "llm.attempt",
    "llm_budget_days": "llm.budget_day",
    "llm_budget_reservations": "llm.budget_reservation",
    "llm_dispatch_receipts": "llm.dispatch_receipt",
    "llm_outcome_events": "llm.outcome_event",
    "agent_tasks": "scheduling.task",
    "jobs": "scheduling.job",
    "bot_actions": "scheduling.action",
    "agent_sessions": "agent.session",
    "agent_messages": "agent.message",
    "agent_memory": "agent.memory",
    "agent_feedback": "agent.feedback",
    "notifications": "notifications.notification",
    "digest_runs": "notifications.digest_run",
}
RETIRED_MODEL_TABLES = frozenset({"oauth_pending"})


def _code(value: object) -> str:
    return str(getattr(value, "name", value)).lower()


async def build_report(*, migrated: bool = False) -> dict[str, object]:
    roles = await Role.objects.values(Role.id, Role.codename).rows()
    role_by_id = {int(role_id): _code(code) for role_id, code in roles}

    legacy_flag = literal(False) if migrated else literal_column("is_superuser")
    user_role_counts = (
        await User.objects.values(User.role_id, legacy_flag, func.count(User.id))
        .group_by(User.role_id, legacy_flag)
        .rows()
    )
    workspace_role_by_id = role_by_id
    if migrated:
        from app.models import WorkspaceRole

        workspace_role_by_id = {
            int(identity): _code(code)
            for identity, code in await WorkspaceRole.objects.values(WorkspaceRole.id, WorkspaceRole.codename).rows()
        }
    membership_role_counts = (
        await TenantUser.objects.values(TenantUser.role_id, func.count(TenantUser.id))
        .group_by(TenantUser.role_id)
        .rows()
    )
    invite_role_counts = (
        await TenantInvite.objects.values(TenantInvite.role_id, func.count(TenantInvite.id))
        .group_by(TenantInvite.role_id)
        .rows()
    )
    model_table_rows = await ModelType.objects.values(ModelType.table_name).rows()
    table_counts = Counter(str(row[0]) for row in model_table_rows)

    user_assignments = Counter()
    elevated_users = 0
    for role_id, legacy_superuser, count in user_role_counts:
        code = role_by_id.get(int(role_id), "unresolved") if role_id is not None else "unresolved"
        user_assignments[code] += int(count)
        if legacy_superuser:
            elevated_users += int(count)

    predicted_platform = Counter()
    for role_id, legacy_superuser, count in user_role_counts:
        code = role_by_id.get(int(role_id), "unresolved") if role_id is not None else "unresolved"
        target = code if migrated else ("admin" if legacy_superuser or code in {"admin", "superuser"} else "user")
        predicted_platform[target] += int(count)

    def counted_roles(rows):
        counts = Counter()
        for role_id, count in rows:
            code = workspace_role_by_id.get(int(role_id), "unresolved") if role_id is not None else "unresolved"
            counts[code] += int(count)
        return dict(sorted(counts.items()))

    retired = sorted(table for table in table_counts if table in RETIRED_MODEL_TABLES)
    unmapped = sorted(table for table in table_counts if table not in RESOURCE_KEYS and table not in RETIRED_MODEL_TABLES)
    return {
        "migration": "0096",
        "predicted_platform_assignments": dict(sorted(predicted_platform.items())),
        "platform_users_by_legacy_role": dict(sorted(user_assignments.items())),
        "users_with_superuser_flag": elevated_users,
        "workspace_memberships_by_legacy_role": counted_roles(membership_role_counts),
        "workspace_invites_by_legacy_role": counted_roles(invite_role_counts),
        "model_type_rows": int(sum(table_counts.values())),
        "model_types_by_resource": {
            RESOURCE_KEYS[table]: int(count) for table, count in sorted(table_counts.items()) if table in RESOURCE_KEYS
        },
        "retired_model_tables": retired[:25],
        "retired_model_table_count": len(retired),
        "retired_model_type_rows": sum(table_counts[table] for table in retired),
        "unmapped_model_tables": unmapped[:25],
        "unmapped_model_table_count": len(unmapped),
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migrated", action="store_true", help="Read the post-transition schema")
    args = parser.parse_args()
    with tenant_scope(bypass=True):
        report = asyncio.run(build_report(migrated=args.migrated))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
