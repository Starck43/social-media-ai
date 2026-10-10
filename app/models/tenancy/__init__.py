"""Canonical tenancy models; the legacy module completes manager binding.

Do not create managers in these leaf modules: tenant_manager imports all four
models back through app.models.tenant while its own module initializes.
"""

from .tenant import Tenant
from .tenant_channel import TenantChannel
from .tenant_invite import TenantInvite
from .tenant_user import TenantUser

__all__ = ["Tenant", "TenantUser", "TenantInvite", "TenantChannel"]
