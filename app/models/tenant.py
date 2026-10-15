"""Legacy tenancy exports; managers bind centrally in app.models.bootstrap."""

from .tenancy import Tenant, TenantChannel, TenantInvite, TenantUser

# isort: split
from .managers.tenant_manager import (
    TenantChannelManager,
    TenantInviteManager,
    TenantManager,
    TenantUserManager,
)
