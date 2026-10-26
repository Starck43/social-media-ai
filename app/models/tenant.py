"""Compatibility aliases; canonical declarations live in domain packages."""

from .managers.tenant_manager import TenantChannelManager, TenantInviteManager, TenantManager, TenantUserManager
from .tenancy import Tenant, TenantChannel, TenantInvite, TenantUser

__all__ = [
    "Tenant",
    "TenantChannel",
    "TenantChannelManager",
    "TenantInvite",
    "TenantInviteManager",
    "TenantManager",
    "TenantUser",
    "TenantUserManager",
]
