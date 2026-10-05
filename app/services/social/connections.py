"""Personal platform connections: is this user authorized, and does it matter?

A source may collect through the platform API (L1, a shared token) or through a
**personal** secret (L2) — a VK user token, a Telegram MTProto session. L2 lives
in the personal vault keyed by `users.id`, so "is it authorized?" is a question
about a *person*, and the person who can fix it is exactly the one whose row it
is.

This module is the single answer to that question. Everything that shows a
status — the navbar, the settings page, the sources list and its banner, the
readiness hint on a source — reads `connection_status()` here rather than
re-deriving "expired?" from a timestamp. That duplication is what produced the
bug the whole page exists to fix: a badge that said "token expired, log in
again" for a token with a live `refresh_token` that the collector renews
automatically every hour.

The states answer **"does this person have to press a button?"**, not "has a
clock run out":

| State | Meaning | Button |
| --- | --- | --- |
| `connected` | secret present and not expired | «Выйти» only |
| `renewable` | access expired, but a `refresh_token` renews it silently | none |
| `reauth` | renewal is impossible — refresh token dead/absent, or revoked | «Войти заново» |
| `missing` | never connected | «Подключить» |
| `unconfigured` | the deployment has no app credentials for this platform | none |

Nothing here decrypts a secret: a status is metadata (`expires_at`, `meta`), so
it is safe to render on every page. A new platform is one `ConnectionSpec` row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# A refresh token whose renewal failed this recently is treated as dead until a
# human looks at it. Without this, every collection run would re-post the same
# broken refresh token — and VK ID invalidates the whole session when a spent
# refresh token is replayed, so retrying silently costs the user their login.
REFRESH_FAILURE_COOLDOWN = timedelta(hours=6)


@dataclass(frozen=True)
class ConnectionSpec:
    """One personal authorization the product knows how to ask for."""

    platform: str
    kind: str
    title: str
    purpose: str
    # `oauth` — the platform runs a browser authorization we redirect to.
    # `manual` — the user pastes a secret; there is nothing to redirect to.
    mode: str
    manual_hint: str = ""

    @property
    def is_oauth(self) -> bool:
        return self.mode == "oauth"


# The registry. Adding a platform is a row here, not a new page.
CONNECTIONS: tuple[ConnectionSpec, ...] = (
    ConnectionSpec(
        platform="vk",
        kind="user_token",
        title="ВКонтакте",
        purpose="Сбор закрытых сообществ и личной стены, где токен сообщества бессилен",
        mode="oauth",
        manual_hint="Можно вставить личный токен вручную, но он не продлевается — OAuth надёжнее.",
    ),
    ConnectionSpec(
        platform="telegram",
        kind="session",
        title="Telegram",
        purpose="Чтение закрытых чатов и истории, куда бот не добавлен",
        mode="manual",
        manual_hint="Нужен api_id и api_hash с my.telegram.org и файл сессии.",
    ),
)

CONNECTIONS_BY_PLATFORM: dict[str, ConnectionSpec] = {spec.platform: spec for spec in CONNECTIONS}


@dataclass(frozen=True)
class ConnectionStatus:
    """What one person must (or must not) do about one platform."""

    platform: str
    state: str
    label: str
    detail: str
    # True only when a person has to press a button. The navbar renders nothing
    # at all unless this is set, so a healthy setup stays silent.
    needs_action: bool
    # Whether a "connect" affordance can work at all (the deployment may lack
    # `VK_APP_ID`, in which case a button would only produce an error).
    can_connect: bool = True

    @property
    def tone(self) -> str:
        """Badge tone for the templates: ok | warn | bad | idle."""
        return {
            "connected": "ok",
            "renewable": "ok",
            "reauth": "bad",
            "missing": "warn",
            "unconfigured": "idle",
        }.get(self.state, "idle")

    @property
    def is_configured(self) -> bool:
        return self.state != "unconfigured"

    @property
    def is_oauth(self) -> bool:
        """Whether the fix is a redirect to the platform or a form in Settings.

        A `manual` platform (Telegram) has no authorize endpoint, so a template
        that links to one regardless offers a button that bounces back with an
        error. Kept here so no template has to know which platform is which.
        """
        spec = CONNECTIONS_BY_PLATFORM.get(self.platform)
        return bool(spec and spec.is_oauth)

    @property
    def fix_href(self) -> str:
        """Where the badge should link when it needs action, else "".

        OAuth goes straight to the platform and comes back to the sources list;
        everything else lands on the connections tab, which holds the manual form.
        """
        if not self.needs_action or not self.can_connect:
            return ""
        return (
            f"/app/connections/{self.platform}/authorize?next=/app/sources"
            if self.is_oauth
            else "/app/settings?tab=connections"
        )


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _human_expiry(expires_at: Optional[datetime]) -> str:
    moment = _utc(expires_at)
    if moment is None:
        return "срок не задан"
    delta = moment - datetime.now(timezone.utc)
    if delta <= timedelta(0):
        return "срок истёк"
    if delta < timedelta(hours=1):
        return f"обновляется через {int(delta.total_seconds() // 60)} мин"
    if delta < timedelta(days=1):
        return f"обновляется через {int(delta.total_seconds() // 3600)} ч"
    return f"действует до {moment:%d.%m.%Y}"


async def _vault_row(user_id: Optional[int], platform: str, kind: str):
    """Newest active vault row for this person/platform, or None."""
    if user_id is None:
        return None
    from app.models.managers.user_credential_manager import user_credentials

    rows = await user_credentials.active(user_id=user_id, platform=platform)
    candidates = [row for row in rows if row.kind == kind]
    if not candidates:
        return None
    candidates.sort(key=lambda row: row.updated_at or row.created_at, reverse=True)
    return candidates[0]


async def connection_status(platform: str, user_id: Optional[int]) -> ConnectionStatus:
    """How `user_id`'s personal access to `platform` stands right now.

    Reads vault metadata only — never `reveal()` — so callers may use this on
    any page, including the shared navbar.
    """
    spec = CONNECTIONS_BY_PLATFORM.get(platform)
    if spec is None:
        return ConnectionStatus(platform, "unconfigured", "Неизвестная платформа", "", False, False)

    row = await _vault_row(user_id, platform, spec.kind)
    if row is None:
        if not spec.is_oauth or not settings.VK_APP_ID:
            # Nothing the user could do about it: the deployment has no app.
            return ConnectionStatus(
                platform,
                "unconfigured",
                "Не настроено оператором",
                "Развертывание не зарегистрировано в VK ID — подключение недоступно",
                False,
                False,
            )
        return ConnectionStatus(
            platform,
            "missing",
            "Не подключено",
            f"Нужен личный доступ: {spec.purpose.lower()}",
            True,
            True,
        )

    meta = row.meta or {}
    expires_at = _utc(row.expires_at)
    now = datetime.now(timezone.utc)

    # A failed renewal is remembered rather than retried silently — see the note
    # on REFRESH_FAILURE_COOLDOWN about VK invalidating a session on replay.
    failed_at = meta.get("refresh_failed_at")
    if failed_at:
        try:
            moment = _utc(datetime.fromisoformat(str(failed_at)))
        except ValueError:
            moment = None
        if moment is not None and now - moment < REFRESH_FAILURE_COOLDOWN:
            return ConnectionStatus(
                platform,
                "reauth",
                "Нужно войти заново",
                "Автоматическое продление не удалось — войдите ещё раз",
                True,
                True,
            )

    if expires_at is None or expires_at > now:
        account = meta.get("user_id")
        who = f" · VK ID {account}" if account else ""
        return ConnectionStatus(
            platform,
            "connected",
            "Подключено",
            f"{spec.title}{who} · {_human_expiry(expires_at)}",
            False,
            True,
        )

    # Expired. Whether that is the user's problem depends entirely on whether a
    # refresh token can renew it — the single distinction this module exists for.
    if meta.get("refresh_token"):
        return ConnectionStatus(
            platform,
            "renewable",
            "Авторизован",
            f"{spec.title} · {_human_expiry(expires_at)}, продление без повторного входа",
            False,
            True,
        )

    if not spec.is_oauth:
        return ConnectionStatus(
            platform,
            "reauth",
            "Нужно войти заново",
            f"Сессия {spec.title} истекла — подключите её заново",
            True,
            True,
        )

    return ConnectionStatus(
        platform,
        "reauth",
        "Нужно войти заново",
        "Токен истёк, и продлить его автоматически нечем",
        True,
        True,
    )


async def connection_statuses(user_id: Optional[int]) -> list[ConnectionStatus]:
    """Every known platform's status for one person, in registry order."""
    return [await connection_status(spec.platform, user_id) for spec in CONNECTIONS]


async def statuses_needing_action(user_id: Optional[int]) -> list[ConnectionStatus]:
    """Only the platforms this person must act on — what the navbar renders."""
    return [status for status in await connection_statuses(user_id) if status.needs_action]


def _platform_key(source: Any) -> str:
    platform = getattr(getattr(source, "platform", None), "platform_type", None)
    if platform is None:
        return ""
    return str(getattr(platform, "db_value", None) or getattr(platform, "value", None) or platform)


def source_connection_spec(source: Any) -> Optional[ConnectionSpec]:
    """The personal connection a source would use, if its platform has one.

    A source on `mode=push` collects through a bot and needs no personal
    secret at all, so asking about a connection would invent a problem.
    """
    if not source_uses_personal_token(source):
        return None
    return CONNECTIONS_BY_PLATFORM.get(_platform_key(source))


def source_uses_personal_token(source: Any) -> bool:
    """True when this source's collection layer can require the owner's L2 secret.

    `pull`/`user` both consult it (as L1 then, where allowed, L2); `push` never
    does. `owner.resolve_source_owner` decides *whose* token, this decides
    whether the question is asked at all.
    """
    mode = (getattr(source, "params", None) or {}).get("mode", "pull")
    return mode in ("pull", "user")


async def source_connection_status(source: Any) -> Optional[ConnectionStatus]:
    """The status of the *owner's* personal access for this source.

    A source may collect with a teammate's token (`Source.params["token_owner"]`),
    so the caller's own connection says nothing about it. Returns None for a
    push source or an ownerless workspace.
    """
    status = await source_connection_statuses([source])
    return status.get(getattr(source, "id", None))


async def source_connection_statuses(sources: Iterable[Any]) -> dict[Any, ConnectionStatus]:
    """`{source.id: ConnectionStatus}` for a whole page of sources.

    A list page asks about every row, and each answer costs two queries: one to
    resolve the owner, one to read the vault. Twenty sources would pay forty, and
    nearly all of them identical — the owner depends only on (tenant,
    `token_owner`) and the status only on (platform, owner). Memoising both turns
    a workspace full of rows sharing one token into two queries.

    Sources that need no personal secret (push) are absent from the mapping, so
    the template renders them as "—" rather than inventing a status for them.
    """
    from app.services.social.owner import resolve_source_owner

    owners: dict[tuple, Optional[int]] = {}
    statuses: dict[tuple, ConnectionStatus] = {}
    result: dict[Any, ConnectionStatus] = {}

    for source in sources:
        spec = source_connection_spec(source)
        if spec is None:
            continue
        owner_key = (getattr(source, "tenant_id", None), (getattr(source, "params", None) or {}).get("token_owner"))
        if owner_key not in owners:
            owners[owner_key] = await resolve_source_owner(source)
        owner_id = owners[owner_key]
        status_key = (spec.platform, owner_id)
        if status_key not in statuses:
            statuses[status_key] = await connection_status(spec.platform, owner_id)
        result[getattr(source, "id", None)] = statuses[status_key]
    return result
