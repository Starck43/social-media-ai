"""Personal-access preflight and the actionable authorisation notice.

A source collects with the personal credential of the member who added it
(`Source.owner_user_id`, or the operator's explicit `params.token_owner`). Two
different facts must not be mixed:

* the member **has not authorised** the platform — collection is blocked before
  it starts, and the fix is one click;
* the member has no personal credential at all, so the run falls back to the
  deployment credential (L1) — collection is **not** blocked, but the view may be
  narrower, and the person must be told so plainly.

`preflight` answers the first question for a set of sources (the confirmation
before a manual run), `service_only` answers the second (the "L1 only" label),
and `notify_authorisation_required` publishes one personal inbox notice per
platform and owner after a run that actually hit `auth_required`. All three read
the same resolver, so the sources page, the task row and the notification cannot
disagree.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from app.services.social.connections import (
    CONNECTIONS_BY_PLATFORM,
    ConnectionStatus,
    authorisation_target,
    blocks_collection,
    is_service_only,
    source_connection_spec,
    source_connection_statuses,
)
from app.services.social.owner import resolve_source_owner

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceAccess:
    """What one source needs from its owner's personal connection."""

    source_id: Any
    platform: str
    # The member whose vault holds the credential; None = deployment credential only.
    owner_user_id: Optional[int]
    status: Optional[ConnectionStatus]

    @property
    def service_only(self) -> bool:
        return is_service_only(self.status)

    @property
    def blocked(self) -> bool:
        return blocks_collection(self.status)


@dataclass(frozen=True)
class PlatformPreflight:
    """One platform's honest answer before a manual run starts."""

    platform: str
    title: str
    # Sources whose owner must authorise: collection would fail without it.
    blocked_source_ids: tuple[Any, ...] = ()
    # Sources that will collect through the deployment credential only.
    service_only_source_ids: tuple[Any, ...] = ()
    owners_by_source: dict[Any, Optional[int]] = field(default_factory=dict)
    operator_source_ids: tuple[Any, ...] = ()

    @property
    def destination(self) -> str:
        return "/app/settings?tab=connections" if self.operator_source_ids else authorisation_target(self.platform)

    @property
    def blocked_count(self) -> int:
        return len(self.blocked_source_ids)

    @property
    def service_only_count(self) -> int:
        return len(self.service_only_source_ids)

    def summary(self) -> str:
        """A sentence a person can act on, or an empty string when silent."""
        parts: list[str] = []
        operator_count = len(self.operator_source_ids)
        personal_count = self.blocked_count - operator_count
        if personal_count:
            parts.append(
                f"{self.title}: {personal_count} источник(ов) не соберутся без личного доступа — авторизуйтесь"
            )
        if operator_count:
            parts.append(f"{self.title}: {operator_count} источник(ов) недоступны — обратитесь к администратору")
        if self.service_only_count:
            parts.append(
                f"{self.title}: {self.service_only_count} источник(ов) соберутся через общий доступ, "
                "личный доступ откроет больше"
            )
        return ". ".join(parts)


async def resolve_access(sources: Iterable[Any]) -> dict[Any, SourceAccess]:
    """Per-source access facts for `sources`, in one pass with cached owners."""
    rows = list(sources)
    from app.services.social.connections import _platform_key
    from app.services.social.connector_authorizations import AuthorizationUnavailable
    from app.services.social.telegram_grant_access import source_grant_selection
    from app.services.social.telegram_modes import is_bot_push

    owners: dict[Any, Optional[int]] = {}
    for row in rows:
        if _platform_key(row) == "telegram":
            try:
                owner = None if is_bot_push(getattr(row, "params", None)) else source_grant_selection(row).owner_user_id
            except AuthorizationUnavailable:
                owner = None  # Never substitute a workspace owner for Telegram.
        else:
            owner = await resolve_source_owner(row)
        owners[getattr(row, "id", None)] = owner
    statuses = await source_connection_statuses(rows, owner_overrides=owners)
    result: dict[Any, SourceAccess] = {}
    for row in rows:
        source_id = getattr(row, "id", None)
        spec = source_connection_spec(row)
        result[source_id] = SourceAccess(
            source_id=source_id,
            platform=spec.platform if spec is not None else "",
            owner_user_id=owners.get(source_id),
            status=statuses.get(source_id),
        )
    return result


async def service_only(sources: Iterable[Any]) -> set[Any]:
    """Ids of sources that will collect through the deployment credential only."""
    return {source_id for source_id, access in (await resolve_access(sources)).items() if access.service_only}


async def preflight(sources: Iterable[Any]) -> list[PlatformPreflight]:
    """What a manual run must say about personal access, per platform.

    `renewal_unverified` is not blocked before an attempt: stored metadata
    does not prove the key is readable or the provider will renew the token.
    """
    blocked: dict[str, list[Any]] = {}
    service: dict[str, list[Any]] = {}
    operator: dict[str, list[Any]] = {}
    owners: dict[Any, Optional[int]] = {}
    for source_id, access in (await resolve_access(sources)).items():
        if access.platform not in CONNECTIONS_BY_PLATFORM or access.status is None:
            continue
        if access.blocked:
            blocked.setdefault(access.platform, []).append(source_id)
            if access.status.operator_attention:
                operator.setdefault(access.platform, []).append(source_id)
        elif access.service_only:
            service.setdefault(access.platform, []).append(source_id)
        else:
            continue
        owners[source_id] = access.owner_user_id
    result: list[PlatformPreflight] = []
    for platform in sorted(set(blocked) | set(service)):
        ids = [*(blocked.get(platform) or []), *(service.get(platform) or [])]
        result.append(
            PlatformPreflight(
                platform=platform,
                title=CONNECTIONS_BY_PLATFORM[platform].title,
                blocked_source_ids=tuple(blocked.get(platform) or ()),
                service_only_source_ids=tuple(service.get(platform) or ()),
                owners_by_source={source_id: owners.get(source_id) for source_id in ids},
                operator_source_ids=tuple(operator.get(platform) or ()),
            )
        )
    return result


@dataclass(frozen=True)
class AuthRequiredReport:
    """One person's authorisation need on one platform, after a real run."""

    platform: str
    owner_user_id: int
    source_count: int
    operator_count: int = 0

    @property
    def title(self) -> str:
        name = (
            CONNECTIONS_BY_PLATFORM[self.platform].title if self.platform in CONNECTIONS_BY_PLATFORM else self.platform
        )
        if self.operator_count:
            return f"{name}: проверьте подключение"
        return f"{name}: нужен ваш личный доступ"

    @property
    def message(self) -> str:
        name = (
            CONNECTIONS_BY_PLATFORM[self.platform].title if self.platform in CONNECTIONS_BY_PLATFORM else self.platform
        )
        if self.operator_count:
            text = f"{self.operator_count} источник(ов) {name} недоступны: обратитесь к администратору."
            if self.source_count > self.operator_count:
                text += f" Для остальных {self.source_count - self.operator_count} источник(ов) требуется личная авторизация."
            return text
        noun = "источнику" if self.source_count % 10 == 1 and self.source_count % 100 != 11 else "источникам"
        return (
            f"{self.source_count} {noun} {name} не собрались: нет вашей личной авторизации. "
            "Подключите аккаунт — сбор продолжится со следующего запуска."
        )

    @property
    def destination(self) -> str:
        return "/app/settings?tab=connections" if self.operator_count else authorisation_target(self.platform)

    @property
    def dedup_key(self) -> str:
        """One row per workspace, platform and owner; a repeated run keeps it."""
        if self.operator_count:
            return f"source-credential-unavailable:{self.platform}:{self.owner_user_id}:{self.source_count}:{self.operator_count}"
        return f"source-auth-required:{self.platform}:{self.owner_user_id}"


async def authorisation_reports(
    sources: Iterable[Any], *, required_source_ids: Iterable[Any] = ()
) -> list[AuthRequiredReport]:
    """Group the sources that actually failed authorisation by owner and platform.

    `required_source_ids` narrows the group to the sources the run reported as
    `auth_required`; without it every blocked source counts.
    """
    wanted = {value for value in required_source_ids}
    counts: dict[tuple[str, int], tuple[int, int]] = {}
    for source_id, access in (await resolve_access(sources)).items():
        if wanted and source_id not in wanted:
            continue
        if not access.blocked or access.owner_user_id is None:
            continue
        key = (access.platform, int(access.owner_user_id))
        total, operator_count = counts.get(key, (0, 0))
        counts[key] = (total + 1, operator_count + int(access.status.operator_attention))
    return [
        AuthRequiredReport(platform=platform, owner_user_id=owner, source_count=count, operator_count=operator_count)
        for (platform, owner), (count, operator_count) in sorted(counts.items())
    ]


async def notify_authorisation_required(reports: Iterable[AuthRequiredReport]) -> int:
    """Publish one personal inbox notice per report. Returns how many were sent.

    Failures are logged, never raised: a missing notice must not turn a
    completed collection into a failed job.
    """
    from app.services.notifications.service import notify
    from app.types import NotificationType

    sent = 0
    for report in reports:
        try:
            await notify.create(
                title=report.title,
                message=report.message,
                ntype=NotificationType.SOURCE_AUTH_REQUIRED,
                entity_type="connection",
                entity_id=None,
                recipient_user_ids=[report.owner_user_id],
                severity="action_required",
                dedup_key=report.dedup_key,
            )
        except Exception:  # noqa: BLE001 — a notice never fails the run
            logger.warning(
                "source_auth_notification_failed platform=%s owner=%s",
                report.platform,
                report.owner_user_id,
                exc_info=True,
            )
            continue
        sent += 1
    return sent
