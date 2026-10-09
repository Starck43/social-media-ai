"""Compatibility entry point for the tenancy models and their managers.

Both app.models.tenant and app.models.tenancy expose the SAME mapped classes.
Keep all four model exports above the manager import: tenant_manager creates
its module-level managers by importing these names back from this module.
The existing per-model objects bindings remain here, after that import returns.
No table, role, plan, invitation or channel-routing behavior changes here.
"""

from .tenancy import Tenant, TenantChannel, TenantInvite, TenantUser


from .managers.tenant_manager import (  # noqa: E402
    TenantChannelManager,
    TenantInviteManager,
    TenantManager,
    TenantUserManager,
)

Tenant.objects = TenantManager()
TenantUser.objects = TenantUserManager()
TenantInvite.objects = TenantInviteManager()
TenantChannel.objects = TenantChannelManager()
