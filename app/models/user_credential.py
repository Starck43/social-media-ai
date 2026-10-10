"""User-scoped third-party credentials (the personal vault).

`user_credentials` holds secrets that belong to a *person*: the L2 (user) tokens
that let the runtime act on behalf of a specific platform account (VK user_token,
Telegram MTProto session). Application/infrastructure secrets (VK app_id,
client_secret, service_token, bot tokens) are one-per-deployment config and are
read from the environment, not stored here. One person may own several
workspaces; their personal tokens live here once and are shared across those
workspaces — never duplicated per tenant.

Security model: the table is deliberately NOT tenant-scoped (like the `Tenant*`
tables) because a user's tokens are valid in every workspace they belong to.
Access control is enforced at the call site (`app.services.social.owner`): a
workspace may only use a token of a user who is an active member of that
workspace (see `tenant_users`), and `Source.params["token_owner"]` records which
user's token a source should use.

The secrets are encrypted at rest (Fernet, `CREDENTIALS_KEY`). Plaintext exists
only as the return value of `resolve_token` — never logged, never echoed into a
chat.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from . import Base, TimestampMixin

if TYPE_CHECKING:
    from .managers.base_manager import BaseManager
    from .managers.user_credential_manager import UserCredentialManager

# isort: split
from .identity.user_credential import UserCredential

# isort: split
from .managers.user_credential_manager import UserCredentialManager  # noqa: E402

UserCredential.objects = UserCredentialManager()
