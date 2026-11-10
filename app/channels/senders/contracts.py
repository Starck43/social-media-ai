"""Small immutable adapter inputs; never serialize account secrets or content."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

EMAIL = "comms.email.send.v1"
TELEGRAM = "comms.telegram.bot_send.v1"
MAX = "comms.max.bot_send.v1"


@dataclass(frozen=True)
class SendBinding:
    """Fresh caller-owned snapshot, NOT proof of database authorization.

    binding_id is a WorkspaceConnection ID for mail or a TenantChannel ID for
    bots. The trusted caller holds ownership/authority locks over the attempt.
    Never construct this snapshot from HTTP/agent-supplied flags.
    """

    workspace_id: int
    binding_id: int
    owner_user_id: int
    actor_user_id: int
    active: bool = False
    verified: bool = False


@dataclass(frozen=True)
class MailAccount:
    binding: SendBinding
    provider_id: str
    account_login: str = field(repr=False)
    app_password: str = field(repr=False)
    send_enabled: bool = False


@dataclass(frozen=True)
class EmailRequest:
    recipient: str = field(repr=False)
    subject: str = field(repr=False)
    text: str = field(repr=False)
    in_reply_to: str | None = field(default=None, repr=False)
    references: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class BotAccount:
    binding: SendBinding
    token: str = field(repr=False)


@dataclass(frozen=True)
class BotRecipient:
    """Evidence of a private chat with THIS bot, resolved by the trusted caller.

    started_bot must come from persisted inbound/consent evidence, not a form.
    A token, numeric user ID or workspace membership alone is not that evidence.
    """

    workspace_id: int
    binding_id: int
    user_id: str = field(repr=False)
    started_bot: bool = False
    kind: str = "private"
    bot_fingerprint: str = ""


@dataclass(frozen=True)
class SendResult:
    outcome: Literal["sent", "blocked", "rejected", "unknown"]
    code: str
    # Only acknowledged provider IDs go here. SMTP acceptance has no such ID.
    receipt_id: str | None = None
    # Locally generated SMTP Message-ID, including on uncertain outcomes.
    message_id: str | None = None


def bot_fingerprint(capability: str, token: str) -> str:
    """Nonsecret binding of inbound consent/review to a specific bot credential.

    Rotation invalidates old evidence. A hash supplied by an untrusted request
    does not establish consent; the caller must resolve its persisted source.
    """
    return hashlib.sha256(f"{capability}\x00{token}".encode("utf-8")).hexdigest()
