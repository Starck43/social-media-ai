"""Provider-neutral native-original contract; no provider is registered here.

Only application-controlled adapters may supply targets after live provider
revalidation. Download/share/bearer URLs are a different capability: they must
never be adapted into browser links. A native URL is not a file revision or a
revocable application grant. No network, secrets, persistence or UI lives here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import unquote, urlsplit

OPEN_TTL = timedelta(seconds=30)
RESOLVE_TIMEOUT = 5.0


class OriginalOpenUnavailable(Exception):
    """Sanitized capability/provider failure without target or source metadata."""

    def __init__(self):
        super().__init__("original_unavailable")


@dataclass(frozen=True, repr=False)
class OriginalOpenContext:
    """Internal context, not a browser selector or bearer authorization.

    Observation is name/type history, NOT a provider content version. An adapter
    must live-revalidate this exact path as a file in this owner connection.
    Renames/replacements cannot be recognized as the same object in this pilot.
    """

    tenant_id: int
    actor_id: int
    connection_id: int
    object_id: int
    path: str
    root_key: str
    policy_key: str
    credential_key: str
    observation_number: int
    issued_at: datetime


@dataclass(frozen=True)
class NativeLinkPolicy:
    """Reviewed exact host/path namespace from application registration only.

    There is intentionally no Yandex/default registration. The provider owner
    must review authenticated native-link semantics before supplying this policy.
    Query strings and fragments are unsupported rather than guessed/rewritten.
    """

    host: str
    path_prefix: str

    def validate(self) -> None:
        if (
            type(self.host) is not str
            or len(self.host) > 253
            or not re.fullmatch(r"[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+", self.host)
            or type(self.path_prefix) is not str
            or not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)+", self.path_prefix)
            or len(self.path_prefix) > 200
        ):
            raise OriginalOpenUnavailable()


@dataclass(frozen=True, repr=False)
class NativeOriginalTarget:
    """Trusted adapter output, never accepted directly from a browser/request.

    verified_at asserts a fresh exact-file provider check, not merely URL syntax.
    The application cannot prove that assertion for an arbitrary injected fake.
    Real registration requires reviewed adapter evidence (P8-T34).
    """

    context: OriginalOpenContext
    url: str = field(repr=False)
    verified_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class ResolvedOriginal:
    """Prepared current-original navigation, NOT evidence a browser opened it.

    Do not log/cache/store the URL. Expiry limits handoff, not the native site's
    session. Revoke blocks subsequent resolution, not a URL already delivered.
    """

    url: str = field(repr=False)
    expires_at: datetime
    version_scope: str = field(default="current", init=False)
    content_version_known: bool = field(default=False, init=False)


class OriginalResolver(Protocol):
    link_policy: NativeLinkPolicy

    async def resolve(self, context: OriginalOpenContext) -> NativeOriginalTarget:
        """Reauthorize provider access to the exact current file, within 5s.

        Credentials/network/DNS/TLS and native-link mapping belong to the adapter.
        Do not synthesize a URL from a stored path or create a public share link.
        """
        ...


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def context_live(context: OriginalOpenContext) -> bool:
    try:
        now = datetime.now(timezone.utc)
        return (
            type(context) is OriginalOpenContext
            and _aware(context.issued_at)
            and context.issued_at <= now < context.issued_at + OPEN_TTL
        )
    except (ValueError, TypeError, OverflowError):
        return False


def validate_target(context, target, policy) -> ResolvedOriginal:
    """Fail closed; never normalize a suspicious link into an allowed one."""
    try:
        if type(policy) is not NativeLinkPolicy:
            raise ValueError
        policy.validate()
        now = datetime.now(timezone.utc)
        if (
            not context_live(context)
            or type(target) is not NativeOriginalTarget
            or type(target.context) is not OriginalOpenContext
            or target.context != context
            or not _aware(target.verified_at)
            or not _aware(target.expires_at)
            or not context.issued_at <= target.verified_at <= now < target.expires_at <= context.issued_at + OPEN_TTL
        ):
            raise ValueError
        url = target.url
        if (
            type(url) is not str
            or not 1 <= len(url) <= 2048
            or not url.isascii()
            or any(ord(char) < 33 or ord(char) == 127 for char in url)
            or any(char in url for char in "\\?#")
            or re.search(r"%(?![0-9a-fA-F]{2})", url)
        ):
            raise ValueError
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.netloc != policy.host
            or not parts.path.startswith(policy.path_prefix)
            or len(parts.path) <= len(policy.path_prefix)
            or parts.query
            or parts.fragment
        ):
            raise ValueError
        for segment in parts.path[1:].split("/"):
            decoded = unquote(segment, encoding="utf-8", errors="strict")
            if (
                not decoded
                or decoded in {".", ".."}
                or any(char in decoded for char in "/\\%?#")
                or any(unicodedata.category(char).startswith("C") for char in decoded)
            ):
                raise ValueError
        return ResolvedOriginal(url, target.expires_at)
    except (ValueError, TypeError, AttributeError, UnicodeError, OverflowError):
        raise OriginalOpenUnavailable() from None
