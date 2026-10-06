"""
Assign model permissions to the platform roles.

Each platform role gets a set of permission *patterns* (wildcards supported,
'!' excludes). The matrix follows the role ladder in `UserRoleType` and is the
canonical source for what each role may do in the operator console (/admin),
the HTTP API (/api) and the CLI.

Run:
    python -m scripts.setup.assign_roles_permissions
"""
import asyncio

from app.core.tenant_context import tenant_scope
from app.services.user.permissions import RolePermissionService

# app.Model permission patterns per role. Wildcards match any action on the
# model (view/create/update/delete/analyze/moderate/export/configure).
# Patterns are lowercase to match the canonical `generate_permission_codename`
# output; `expand_permission_patterns` matches case-insensitively.
ROLE_PERMISSIONS = {
    "VIEWER": [
        "social.source.view",
        "social.platform.view",
        "social.notification.view",
        "dashboard.aianalytics.view",
        "social.digestrun.view",
        "account.credential.view",
        "account.llmprovider.view",
    ],
    "AI_BOT": [
        "social.source.view",
        "social.platform.view",
        "social.notification.view",
        "dashboard.aianalytics.view",
        "social.digestrun.view",
        "social.agentmessage.*",
        "social.agentsession.*",
        "social.agentscenario.*",
        "social.agenttask.*",
        "social.botaction.*",
        "account.credential.view",
        "account.llmprovider.view",
    ],
    "MANAGER": [
        "social.source.*",
        "social.platform.*",
        "social.notification.*",
        "dashboard.aianalytics.view",
        "social.digestrun.view",
        "account.credential.view",
        "account.credential.update",
        "account.llmprovider.view",
    ],
    "ANALYST": [
        "social.source.view",
        "social.source.analyze",
        "social.source.export",
        "social.notification.view",
        "dashboard.aianalytics.*",
        "social.digestrun.*",
        "account.credential.view",
        "account.llmprovider.view",
    ],
    "MODERATOR": [
        "social.source.*",
        "social.platform.*",
        "social.notification.*",
        "dashboard.aianalytics.*",
        "social.digestrun.view",
        "social.agentfeedback.*",
        "account.credential.view",
        "account.llmprovider.view",
    ],
    "ADMIN": ["*"],
    "SUPERUSER": ["*"],
}


async def assign_roles_permissions() -> dict[str, list[str]]:
    results = {}
    for role_codename, patterns in ROLE_PERMISSIONS.items():
        result = await RolePermissionService.update_role_permissions(
            role_codename=role_codename.lower(),
            permission_codenames=patterns,
            strategy="synchronize",
        )
        results[role_codename] = result
    return results


if __name__ == "__main__":
    with tenant_scope(bypass=True):
        out = asyncio.run(assign_roles_permissions())
    for role, result in out.items():
        added = result["added"]
        print(f"{role}: {len(added)} permissions assigned")
