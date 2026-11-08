"""P14-T05 & P14-T06 unit, SQL-backed, and HTTP contract tests for greeting delivery, batch messaging, consent, and unsubscribe.

Covers:
- P14-T05 (`CrmGreetingsService.send_greeting` / `CrmMessagingService.send_greeting`):
  - Schedules approved birthday/anniversary greetings over `email`, `telegram`, and `max`
    through the P15-T05 `OutboxStore` (`draft` + `schedule`) without creating a second outbox or sender (D10).
  - Preserves `GreetingHistory.draft_text_encrypted` as the clean greeting text while storing the
    signed unsubscribe footer only on `OutboxMessage.content_encrypted`.
  - Enforces D7 channel restrictions (`email`, `telegram`, `max` only; rejects `phone`, `bitrix24`, `mtproto`).
  - Enforces D15 explicit opt-in consent for `purpose="greeting"`: a channel/identity on a Bitrix24 CRM card
    without a recorded `ContactConsent` row is rejected with `403 consent_required`.
  - Prevents repeat greetings for the same `(tenant_id, contact_id, event_type, year)`
    (`409 greeting_already_processed`).
  - Synchronizes delivery status (`approved`, `sent`, `failed`, `unknown`, `skipped`) from `OutboxTarget`.
- P14-T06 (`CrmMessagingService.send_batch_messages`, `get_batch_status`, `process_one_click_unsubscribe`,
  `record_manual_consent`):
  - Personalizes `{name}` and `{company}` single-pass (no recursive expansion, no CR/LF injection in subject).
  - Handles 100+ contacts in a batch (`120` contacts tested), filtering out unconsented or withdrawn contacts
    and returning an explicit `coverage_warning` + `skipped_no_consent_count` (D15).
  - Separates `OutboxConflict` (e.g., `outbox_idempotency_mismatch`) into `skipped_conflict`
    (`skipped_conflict_count`, `skipped_conflict_contact_ids`) distinct from `skipped_no_route`.
  - Emits absolute `http(s)://` unsubscribe URLs using `settings.PUBLIC_BASE_URL` and fails closed (`503`)
    if `PUBLIC_BASE_URL` or `SECRET_KEY` is unconfigured.
  - Enforces TTL (`UNSUBSCRIBE_TOKEN_TTL_SECONDS`) and rejects expired or future-`iat` unsubscribe tokens.
  - Records unsubscribe consent withdrawal through `ContactManager.record_consent` (with `Contact` row lock),
    preserves `withdraw -> grant -> withdraw` ordering, cancels only current-year (`effective_now.year`)
    unclaimed outbox targets and greeting rows, and returns a uniform `422 invalid_unsubscribe_token`
    for both invalid tokens and deleted/merged contacts.
  - Enforces strict tenant isolation in `_resolve_contact_identity`.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

_SIGNING_SEED = "unit-test-" + ("ab" * 16)
os.environ.setdefault("SECRET_KEY", _SIGNING_SEED)
os.environ.setdefault("POSTGRES_URL", "postgresql+asyncpg://localhost:5432/test_db")
os.environ.setdefault("CREDENTIALS_KEY", Fernet.generate_key().decode("ascii"))
os.environ.setdefault("PII_ENCRYPTION_KEYS", Fernet.generate_key().decode("ascii"))
os.environ.setdefault("PII_INDEX_KEY", Fernet.generate_key().decode("ascii"))


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


from app.api.v1.endpoints import bitrix as bitrix_endpoints  # noqa: E402
from app.core import api_scope  # noqa: E402
from app.core.access_catalog import workspace_grants  # noqa: E402
from app.core.api_scope import ApiScopeMiddleware  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.permissions import operator_permission_scope, permission_scope  # noqa: E402
from app.core.tenant_context import tenant_scope  # noqa: E402
from app.integrations.bitrix24.client import Bitrix24Error  # noqa: E402
from app.models import (  # noqa: E402
    Contact,
    ContactConsent,
    ContactIdentity,
    Tenant,
    TenantUser,
    User,
    WorkspaceConnection,
    WorkspaceConnectionSecret,
    WorkspaceRole,
)
from app.models.actions.business_command import BusinessCommand, BusinessCommandTarget  # noqa: E402
from app.models.audit.action_audit_event import ActionAuditEvent  # noqa: E402
from app.models.communications.outbox import OutboxMessage, OutboxTarget  # noqa: E402
from app.models.crm.entity import CrmEntity  # noqa: E402
from app.models.crm.greeting_history import GreetingHistory  # noqa: E402
from app.models.managers.contact_manager import ContactManager  # noqa: E402
from app.services.commands import ledger  # noqa: E402
from app.services.commands.registry import canonical_payload_hash  # noqa: E402
from app.services.communications.outbox_contracts import DraftIn, ScheduleIn  # noqa: E402
from app.services.communications.outbox_store import OutboxConflict, OutboxStore  # noqa: E402
from app.services.crm.greetings import CrmGreetingsService  # noqa: E402
from app.services.crm.messaging import (  # noqa: E402
    CrmMessagingService,
    _resolve_contact_identity,
    append_unsubscribe_footer,
    build_unsubscribe_url,
    create_unsubscribe_token,
    has_active_consent,
    render_personalized_template,
    verify_unsubscribe_token,
)
from app.services.user.auth import get_authenticated_user  # noqa: E402
from app.types import ActionType  # noqa: E402
from app.utils.crypto import encrypt_secret  # noqa: E402

__source_only__ = True

UTC = timezone.utc

_SQLITE_MODELS = (
    Tenant,
    User,
    WorkspaceRole,
    TenantUser,
    WorkspaceConnection,
    WorkspaceConnectionSecret,
    Contact,
    ContactIdentity,
    ContactConsent,
    CrmEntity,
    GreetingHistory,
    BusinessCommand,
    BusinessCommandTarget,
    OutboxMessage,
    OutboxTarget,
)


def _ensure_tz(target: Any, *_args: Any, **_kwargs: Any) -> None:
    for key, val in list(getattr(target, "__dict__", {}).items()):
        if isinstance(val, datetime) and val.tzinfo is None:
            target.__dict__[key] = val.replace(tzinfo=UTC)


for _model in _SQLITE_MODELS:
    event.listen(_model, "load", _ensure_tz)
    event.listen(_model, "refresh", _ensure_tz)


@pytest.fixture(autouse=True)
def _pii_and_runtime_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(settings, "PII_INDEX_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(settings, "SECRET_KEY", _SIGNING_SEED)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://crm.example.com")
    monkeypatch.setattr(settings, "UNSUBSCRIBE_TOKEN_TTL_SECONDS", 90 * 86400)
    monkeypatch.setattr(
        ledger,
        "RUNTIME_ENABLED_CAPABILITIES",
        {"comms.email.send.v1", "comms.telegram.bot_send.v1", "comms.max.bot_send.v1"},
    )
    monkeypatch.setattr(ledger, "RUNTIME_KILL_SWITCHES", {})


class SqliteMessagingSession:
    """Real SQLite-backed SQLAlchemy session executing real SQL queries, joins, and locks."""

    def __init__(self, *, seed_defaults: bool = True) -> None:
        raw_engine = create_engine("sqlite:///:memory:")

        @event.listens_for(raw_engine, "connect")
        def _register_regexp(dbapi_conn: Any, _record: Any) -> None:
            dbapi_conn.create_function("regexp", 2, lambda pat, val: bool(re.search(pat, val or "")))

        @event.listens_for(raw_engine, "before_cursor_execute", retval=True)
        def _strip_pg_clauses(
            _conn: Any,
            _cursor: Any,
            statement: str,
            parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> tuple[str, Any]:
            statement = re.sub(r"::jsonb?", "", statement)
            statement = re.sub(
                r"\s+FOR\s+(UPDATE|SHARE)(\s+NOWAIT|\s+SKIP\s+LOCKED)?",
                "",
                statement,
                flags=re.IGNORECASE,
            )
            statement = re.sub(
                r"([a-zA-Z0-9_.]+)\s*~\s*('[^']*')",
                r"regexp(\2, \1)",
                statement,
            )
            return statement, parameters

        self._engine = raw_engine.execution_options(schema_translate_map={settings.DB_SCHEMA: None, "public": None})
        for model in _SQLITE_MODELS:
            model.__table__.create(self._engine, checkfirst=True)
        self._s = Session(self._engine, expire_on_commit=False)

        if seed_defaults:
            self._seed_workspace()

    def _seed_workspace(self) -> None:
        self._s.add(Tenant(id=10, name="Tenant 10", slug="tenant-10", is_active=True))
        self._s.add(Tenant(id=20, name="Tenant 20", slug="tenant-20", is_active=True))
        self._s.add(WorkspaceRole(id=1, codename="owner"))
        self._s.add(
            User(
                id=101,
                username="owner101",
                email="owner101@example.com",
                hashed_password="x",
                is_active=True,
                role_id=1,
            )
        )
        self._s.add(
            User(
                id=42,
                username="manager42",
                email="manager42@example.com",
                hashed_password="x",
                is_active=True,
                role_id=1,
            )
        )
        self._s.add(
            TenantUser(
                id=1,
                tenant_id=10,
                user_id=101,
                role_id=1,
                channel="web",
                external_user_id="101",
                is_active=True,
            )
        )
        self._s.add(
            TenantUser(
                id=2,
                tenant_id=10,
                user_id=42,
                role_id=1,
                channel="web",
                external_user_id="42",
                is_active=True,
            )
        )
        for conn_id, platform, provider_id, auth_method, login_key in (
            (5, "email", "mailru_mail", "app_password", "smtp@example.com"),
            (6, "telegram", "telegram_bot", "bot_token", "tg_bot_6"),
            (7, "max", "max_bot", "bot_token", "max_bot_7"),
        ):
            self._s.add(
                WorkspaceConnection(
                    id=conn_id,
                    tenant_id=10,
                    owner_user_id=101,
                    platform=platform,
                    provider_id=provider_id,
                    auth_method=auth_method,
                    schema_version=1,
                    label=f"Conn {conn_id}",
                    account_key=login_key,
                    config={"account_login": login_key, "send_enabled": True},
                    state="verified",
                    is_active=True,
                )
            )
            self._s.add(
                WorkspaceConnectionSecret(
                    connection_id=conn_id,
                    secret_encrypted=encrypt_secret(f"secret-{conn_id}"),
                )
            )
        self._s.flush()

    def add(self, obj: Any) -> None:
        self._s.add(obj)
        self._s.flush()
        _ensure_tz(obj)

    async def flush(self) -> None:
        self._s.flush()
        for obj in list(self._s.identity_map.values()):
            _ensure_tz(obj)

    async def refresh(self, obj: Any) -> None:
        self._s.refresh(obj)
        _ensure_tz(obj)

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        res = self._s.execute(stmt, *args, **kwargs)
        for obj in list(self._s.identity_map.values()):
            _ensure_tz(obj)
        return res

    async def get(self, entity: Any, ident: Any, **kwargs: Any) -> Any:
        obj = self._s.get(entity, ident, **kwargs)
        if obj is not None:
            _ensure_tz(obj)
        return obj

    async def scalar(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        val = self._s.scalar(stmt, *args, **kwargs)
        if val is not None and hasattr(val, "__dict__"):
            _ensure_tz(val)
        return val

    async def scalars(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        res = self._s.scalars(stmt, *args, **kwargs)
        for obj in list(self._s.identity_map.values()):
            _ensure_tz(obj)
        return res

    @property
    def greetings(self) -> dict[int, GreetingHistory]:
        return {g.id: g for g in self._s.scalars(select(GreetingHistory)).all()}

    @property
    def outbox_messages(self) -> dict[int, OutboxMessage]:
        return {m.id: m for m in self._s.scalars(select(OutboxMessage)).all()}

    @property
    def outbox_targets(self) -> dict[int, OutboxTarget]:
        return {t.id: t for t in self._s.scalars(select(OutboxTarget)).all()}

    @property
    def consents(self) -> dict[int, ContactConsent]:
        return {c.id: c for c in self._s.scalars(select(ContactConsent)).all()}


class SqliteOutboxStoreAdapter:
    """SQLite-backed adapter persisting real `OutboxMessage`, `OutboxTarget`, and `BusinessCommand` rows."""

    def __init__(self, *, tenant_id: int = 10, author_id: int = 101) -> None:
        self.tenant_id = tenant_id
        self.author_id = author_id
        self.draft_calls: list[tuple[DraftIn, str]] = []
        self.schedule_calls: list[ScheduleIn] = []

    async def draft(self, data: DraftIn, idempotency_key: str, *, session: SqliteMessagingSession) -> dict[str, Any]:
        self.draft_calls.append((data, idempotency_key))
        target_refs = [f"{t.channel}:{t.connection_id}:{t.identity_id}" for t in data.targets]
        content_hash = canonical_payload_hash(
            "comms.email.send.v1",
            target_refs,
            {
                "contact_id": data.contact_id,
                "subject": data.subject,
                "body": data.body,
                "purpose": data.purpose,
                "targets": [t.model_dump() for t in data.targets],
            },
        )
        existing = (
            await session.execute(
                select(OutboxMessage).where(
                    OutboxMessage.tenant_id == self.tenant_id,
                    OutboxMessage.author_id == self.author_id,
                    OutboxMessage.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.request_hash != content_hash:
                raise OutboxConflict("outbox_idempotency_mismatch")
            targets = list(
                (
                    await session.execute(
                        select(OutboxTarget).where(
                            OutboxTarget.tenant_id == self.tenant_id,
                            OutboxTarget.message_id == existing.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            return {
                "id": existing.id,
                "tenant_id": existing.tenant_id,
                "author_id": existing.author_id,
                "contact_id": existing.contact_id,
                "purpose": existing.purpose,
                "state": existing.state,
                "content_hash": existing.content_hash,
                "targets": [
                    {
                        "id": t.id,
                        "channel": t.channel,
                        "connection_id": t.connection_id,
                        "identity_id": t.identity_id,
                        "state": t.state,
                        "command_id": t.command_id,
                    }
                    for t in targets
                ],
            }

        msg = OutboxMessage(
            tenant_id=self.tenant_id,
            author_id=self.author_id,
            contact_id=data.contact_id,
            purpose=data.purpose,
            state="validated",
            timezone=data.timezone,
            subject_encrypted=data.subject,
            content_encrypted=data.body,
            content_hash=content_hash,
            request_hash=content_hash,
            idempotency_key=idempotency_key,
        )
        session.add(msg)
        target_dicts = []
        for t_in in data.targets:
            tgt = OutboxTarget(
                tenant_id=self.tenant_id,
                message_id=msg.id,
                channel=t_in.channel,
                connection_id=t_in.connection_id,
                identity_id=t_in.identity_id,
                recipient_encrypted=f"{t_in.channel}:{t_in.identity_id}",
                credential_hash=content_hash,
                content_hash=content_hash,
                idempotency_key=f"{idempotency_key}:{t_in.channel}:{t_in.identity_id}",
                state="validated",
                attempts=0,
            )
            session.add(tgt)
            target_dicts.append(
                {
                    "id": tgt.id,
                    "channel": tgt.channel,
                    "connection_id": tgt.connection_id,
                    "identity_id": tgt.identity_id,
                    "state": tgt.state,
                    "command_id": None,
                }
            )
        await session.flush()
        return {
            "id": msg.id,
            "tenant_id": msg.tenant_id,
            "author_id": msg.author_id,
            "contact_id": msg.contact_id,
            "purpose": msg.purpose,
            "state": msg.state,
            "content_hash": msg.content_hash,
            "targets": target_dicts,
        }

    async def schedule(self, data: ScheduleIn, *, session: SqliteMessagingSession) -> dict[str, Any]:
        self.schedule_calls.append(data)
        msg = (await session.execute(select(OutboxMessage).where(OutboxMessage.id == data.message_id))).scalar_one()
        assert msg.content_hash == data.expected_content_hash
        now = datetime.now(UTC)
        msg.state = "approved"
        msg.approved_by = self.author_id
        msg.approved_hash = msg.content_hash
        msg.scheduled_at = data.scheduled_at
        msg.approval_expires_at = data.scheduled_at + timedelta(seconds=data.ttl_seconds)

        targets = list(
            (await session.execute(select(OutboxTarget).where(OutboxTarget.message_id == msg.id))).scalars().all()
        )
        target_dicts = []
        for tgt in targets:
            target_ref = f"{tgt.channel}:{tgt.connection_id}:{tgt.identity_id}"
            cap = "comms.email.send.v1" if tgt.channel == "email" else f"comms.{tgt.channel}.bot_send.v1"
            cmd = BusinessCommand(
                tenant_id=self.tenant_id,
                actor_user_id=self.author_id,
                capability=cap,
                approval_policy="per_item",
                state="approved",
                idempotency_key=f"outbox-target-{tgt.id}",
                target_refs=[target_ref],
                payload={"message_id": msg.id, "target_id": tgt.id},
                payload_hash=msg.content_hash,
                approved_payload_hash=msg.content_hash,
                approved_user_id=self.author_id,
                approved_at=now,
                approval_expires_at=msg.approval_expires_at,
                preview_snapshot={},
            )
            session.add(cmd)
            session.add(
                BusinessCommandTarget(
                    tenant_id=self.tenant_id,
                    command_id=cmd.id,
                    target_ref=target_ref,
                    target_idempotency_key=f"outbox-target-{tgt.id}",
                    state="pending",
                )
            )
            tgt.state = "approved"
            tgt.command_id = cmd.id
            tgt.dispatch_after = data.scheduled_at
            target_dicts.append(
                {
                    "id": tgt.id,
                    "channel": tgt.channel,
                    "connection_id": tgt.connection_id,
                    "identity_id": tgt.identity_id,
                    "state": tgt.state,
                    "command_id": tgt.command_id,
                }
            )
        await session.flush()
        return {
            "id": msg.id,
            "state": msg.state,
            "scheduled_at": msg.scheduled_at,
            "targets": target_dicts,
        }


def test_p14_t06_personalization_and_unsubscribe_token_ttl_secret_and_footer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Single-pass replacement: {name} containing "{company}" must NOT expand recursively
    rendered = render_personalized_template(
        "Здравствуйте, {name} из {company}!",
        name="Агент {company}",
        company="ООО Альфа",
    )
    assert rendered == "Здравствуйте, Агент {company} из ООО Альфа!"

    # 2. Subject single-line sanitization strips CR/LF
    subj = render_personalized_template(
        "Тема для {name}\r\nBcc: attacker@example.com",
        name="Иван\nПетров",
        single_line=True,
    )
    assert "\r" not in subj and "\n" not in subj

    # 3. Blocker 1: Fresh token succeeds; expired token (e.g. from 2025) and future iat fail with 422
    now_dt = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    now_ts = int(now_dt.timestamp())
    fresh_token = create_unsubscribe_token(
        tenant_id=10,
        contact_id=42,
        channel="email",
        purpose="marketing",
        issued_at=now_ts,
    )
    verified = verify_unsubscribe_token(fresh_token, now=now_dt)
    assert verified == {
        "tenant_id": 10,
        "contact_id": 42,
        "channel": "email",
        "purpose": "marketing",
        "issued_at": now_ts,
    }

    token_2025 = create_unsubscribe_token(
        tenant_id=10,
        contact_id=42,
        channel="email",
        purpose="marketing",
        issued_at=int(datetime(2025, 6, 1, tzinfo=UTC).timestamp()),
    )
    with pytest.raises(Bitrix24Error) as exc_old:
        verify_unsubscribe_token(token_2025, now=now_dt)
    assert exc_old.value.code == "invalid_unsubscribe_token"
    assert exc_old.value.status_code == 422

    token_future = create_unsubscribe_token(
        tenant_id=10,
        contact_id=42,
        channel="email",
        purpose="marketing",
        issued_at=now_ts + 3600,
    )
    with pytest.raises(Bitrix24Error) as exc_future:
        verify_unsubscribe_token(token_future, now=now_dt)
    assert exc_future.value.code == "invalid_unsubscribe_token"
    assert exc_future.value.status_code == 422

    # 4. Tampered signature is rejected
    payload_part, sig_part = fresh_token.split(".", 1)
    bad_token = f"{payload_part}.{'A' if sig_part[0] != 'A' else 'B'}{sig_part[1:]}"
    with pytest.raises(Bitrix24Error) as exc_info:
        verify_unsubscribe_token(bad_token, now=now_dt)
    assert exc_info.value.code == "invalid_unsubscribe_token"
    assert exc_info.value.status_code == 422

    # 5. Blocker 2: Empty or whitespace SECRET_KEY fails closed with 503 (never falls back to a default key)
    monkeypatch.setattr(settings, "SECRET_KEY", "   ")
    with pytest.raises(Bitrix24Error) as exc_no_secret_create:
        create_unsubscribe_token(
            tenant_id=10,
            contact_id=42,
            channel="email",
            purpose="marketing",
            issued_at=now_ts,
        )
    assert exc_no_secret_create.value.code == "unsubscribe_secret_unconfigured"
    assert exc_no_secret_create.value.status_code == 503

    with pytest.raises(Bitrix24Error) as exc_no_secret_verify:
        verify_unsubscribe_token(fresh_token, now=now_dt)
    assert exc_no_secret_verify.value.code == "unsubscribe_secret_unconfigured"
    assert exc_no_secret_verify.value.status_code == 503
    monkeypatch.setattr(settings, "SECRET_KEY", _SIGNING_SEED)

    # 6. Blocker 3 & 4: Absolute unsubscribe URL with host & no '/stop' promise in footer
    abs_url = build_unsubscribe_url(fresh_token)
    assert abs_url.startswith("https://crm.example.com/api/v1/integrations/bitrix/messaging/unsubscribe?token=")
    for ch in ("email", "telegram", "max"):
        footer_text = append_unsubscribe_footer("Привет!", unsubscribe_url=abs_url, channel=ch)
        assert abs_url in footer_text
        assert "/stop" not in footer_text

    # Relative or blank PUBLIC_BASE_URL fails closed with 503
    with pytest.raises(Bitrix24Error) as exc_bad_host:
        build_unsubscribe_url(fresh_token, base_url="/relative/path")
    assert exc_bad_host.value.code == "unsubscribe_base_url_unconfigured"
    assert exc_bad_host.value.status_code == 503

    # Public API allowlist includes /api/v1/integrations/bitrix/messaging/unsubscribe
    assert api_scope._is_public("/api/v1/integrations/bitrix/messaging/unsubscribe") is True


@pytest.mark.asyncio
async def test_p14_t05_greeting_delivery_via_email_telegram_and_max_with_d15_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SqliteMessagingSession()
    fake_outbox = SqliteOutboxStoreAdapter(tenant_id=10, author_id=101)
    now = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", AsyncMock(return_value=SimpleNamespace(id=1)))

    # Contact with email, telegram_id, and max_id on CRM card, but NO consent yet
    c1 = Contact(
        id=1,
        tenant_id=10,
        display_name_encrypted="Екатерина Смирнова",
        birthday_encrypted="1992-11-11",
        birthday_has_year=True,
        anniversary_encrypted="2021-11-15",
        anniversary_has_year=True,
        preferred_channel="email",
    )
    session.add(c1)
    session.add(
        ContactIdentity(
            id=11,
            tenant_id=10,
            contact_id=1,
            kind="email",
            value_encrypted="ekaterina@example.com",
            blind_index="1" * 64,
            connection_id=5,
            verified=True,
        )
    )
    session.add(
        ContactIdentity(
            id=12,
            tenant_id=10,
            contact_id=1,
            kind="telegram_id",
            value_encrypted="99887766",
            blind_index="2" * 64,
            connection_id=6,
            verified=True,
        )
    )
    session.add(
        ContactIdentity(
            id=13,
            tenant_id=10,
            contact_id=1,
            kind="max_id",
            value_encrypted="max_554433",
            blind_index="3" * 64,
            connection_id=7,
            verified=True,
        )
    )

    greetings_svc = CrmGreetingsService()
    messaging_svc = CrmMessagingService(outbox=fake_outbox)

    with tenant_scope(10):
        # 1. Generate birthday greeting draft
        draft = await greetings_svc.generate_greeting_draft(
            tenant_id=10,
            actor_id=101,
            contact_id=1,
            event_type="birthday",
            year=2026,
            channel="email",
            reference_date=date(2026, 11, 8),
            session=session,
        )
        assert draft["status"] == "draft"
        original_draft_text = draft["draft_text"]

        # 2. D15: Attempting to send without a recorded ContactConsent row for purpose='greeting' fails with 403
        with pytest.raises(Bitrix24Error) as exc_no_consent:
            await messaging_svc.send_greeting(
                tenant_id=10,
                actor_id=101,
                connection_id=5,
                greeting_id=draft["id"],
                now=now,
                session=session,
            )
        assert exc_no_consent.value.code == "consent_required"
        assert exc_no_consent.value.status_code == 403
        assert len(fake_outbox.draft_calls) == 0

        # 3. D7: Disallowed channel ('phone' or 'bitrix24') is rejected with 422
        with pytest.raises(Bitrix24Error) as exc_bad_channel:
            await messaging_svc.send_greeting(
                tenant_id=10,
                actor_id=101,
                connection_id=5,
                greeting_id=draft["id"],
                channel="phone",
                now=now,
                session=session,
            )
        assert exc_bad_channel.value.code == "invalid_channel"
        assert exc_bad_channel.value.status_code == 422

        # 4. Grant opt-in consent for email/telegram/max with purpose='greeting'
        for ch in ("email", "telegram", "max"):
            session.add(
                ContactConsent(
                    tenant_id=10,
                    contact_id=1,
                    channel=ch,
                    purpose="greeting",
                    basis_encrypted="client_opt_in",
                    source_evidence_encrypted=f"form:{ch}",
                    granted_at=now - timedelta(days=1),
                )
            )

        # 5. Send birthday greeting via email through OutboxStore
        sent_email = await messaging_svc.send_greeting(
            tenant_id=10,
            actor_id=101,
            connection_id=5,
            greeting_id=draft["id"],
            channel="email",
            now=now,
            session=session,
        )
        assert sent_email["status"] == "approved"
        assert sent_email["channel"] == "email"
        assert sent_email["command_id"] is not None
        assert sent_email["unsubscribe_url"].startswith(
            "https://crm.example.com/api/v1/integrations/bitrix/messaging/unsubscribe?token="
        )
        assert sent_email["unsubscribe_url"] in fake_outbox.draft_calls[-1][0].body
        # Medium 2: GreetingHistory.draft_text_encrypted is NOT overwritten with the unsubscribe URL
        assert session.greetings[draft["id"]].draft_text_encrypted == original_draft_text
        assert "unsubscribe?token=" not in session.greetings[draft["id"]].draft_text_encrypted

        # 6. Repeat send for the same greeting in the same year is refused with 409
        with pytest.raises(Bitrix24Error) as exc_dup:
            await messaging_svc.send_greeting(
                tenant_id=10,
                actor_id=101,
                connection_id=5,
                greeting_id=draft["id"],
                channel="email",
                now=now,
                session=session,
            )
        assert exc_dup.value.code == "greeting_already_processed"
        assert exc_dup.value.status_code == 409

        # 7. Simulate P15-T06 sender completing delivery -> get_greeting_delivery_status updates status to 'sent'
        outbox_tgt = session.outbox_targets[sent_email["outbox_target_id"]]
        outbox_tgt.attempts = 1
        outbox_tgt.claim_token = "claim-tok-001"
        outbox_tgt.claimed_at = now
        outbox_tgt.state = "sent"
        outbox_tgt.provider_message_id = "<smtp-msg-001@example.com>"
        await session.flush()
        status_out = await messaging_svc.get_greeting_delivery_status(
            tenant_id=10,
            actor_id=101,
            greeting_id=draft["id"],
            session=session,
        )
        assert status_out["status"] == "sent"
        assert status_out["provider_message_id"] == "<smtp-msg-001@example.com>"
        assert status_out["sent_at"] is not None

        # 8. Deliver anniversary greeting over Telegram (year=2026) and MAX (year=2027)
        draft_tg = await greetings_svc.generate_greeting_draft(
            tenant_id=10,
            actor_id=101,
            contact_id=1,
            event_type="anniversary",
            year=2026,
            channel="telegram",
            reference_date=date(2026, 11, 8),
            session=session,
        )
        sent_tg = await messaging_svc.send_greeting(
            tenant_id=10,
            actor_id=101,
            connection_id=6,
            greeting_id=draft_tg["id"],
            channel="telegram",
            now=now,
            session=session,
        )
        assert sent_tg["channel"] == "telegram"
        assert sent_tg["status"] == "approved"

        draft_max = await greetings_svc.generate_greeting_draft(
            tenant_id=10,
            actor_id=101,
            contact_id=1,
            event_type="anniversary",
            year=2027,
            channel="max",
            reference_date=date(2026, 11, 8),
            session=session,
        )
        sent_max = await messaging_svc.send_greeting(
            tenant_id=10,
            actor_id=101,
            connection_id=7,
            greeting_id=draft_max["id"],
            channel="max",
            now=now,
            session=session,
        )
        assert sent_max["channel"] == "max"
        assert sent_max["status"] == "approved"


@pytest.mark.asyncio
async def test_p14_t06_batch_120_contacts_personalization_coverage_warning_and_unsubscribe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SqliteMessagingSession()
    fake_outbox = SqliteOutboxStoreAdapter(tenant_id=10, author_id=101)
    now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", AsyncMock(return_value=SimpleNamespace(id=99)))

    # Company entity in CRM
    session.add(
        CrmEntity(
            id=500,
            tenant_id=10,
            connection_id=5,
            provider="bitrix24",
            entity_kind="company",
            external_id="comp-1",
            last_synced_at=now,
            title_encrypted="ООО Северсталь-Партнёр",
        )
    )

    # Create 120 contacts:
    # - Contacts 1..100: active 'marketing' consent + verified email identity
    # - Contacts 101..115: Bitrix24 CRM card + verified email identity, but NO ContactConsent row (D15 candidate only)
    # - Contacts 116..120: granted consent followed by a later withdrawal
    contact_ids: list[int] = []
    for i in range(1, 121):
        contact_ids.append(i)
        session.add(
            Contact(
                id=i,
                tenant_id=10,
                display_name_encrypted=f"Клиент #{i}",
                preferred_channel="email",
            )
        )
        session.add(
            ContactIdentity(
                id=1000 + i,
                tenant_id=10,
                contact_id=i,
                kind="email",
                value_encrypted=f"client{i}@example.com",
                blind_index=f"{i:064x}",
                connection_id=5,
                verified=True,
            )
        )
        session.add(
            CrmEntity(
                id=2000 + i,
                tenant_id=10,
                connection_id=5,
                provider="bitrix24",
                entity_kind="contact",
                external_id=f"bx-{i}",
                contact_id=i,
                company_external_id="comp-1",
                last_synced_at=now,
                title_encrypted=f"Клиент #{i}",
            )
        )
        if i <= 100:
            session.add(
                ContactConsent(
                    tenant_id=10,
                    contact_id=i,
                    channel="email",
                    purpose="marketing",
                    basis_encrypted="opt_in_form",
                    source_evidence_encrypted=f"web_form_{i}",
                    granted_at=now - timedelta(days=5),
                )
            )
        elif i >= 116:
            session.add(
                ContactConsent(
                    tenant_id=10,
                    contact_id=i,
                    channel="email",
                    purpose="marketing",
                    basis_encrypted="opt_in_form",
                    source_evidence_encrypted=f"web_form_{i}",
                    granted_at=now - timedelta(days=10),
                    withdrawn_at=now - timedelta(days=1),
                )
            )

    svc = CrmMessagingService(outbox=fake_outbox)
    with tenant_scope(10):
        batch = await svc.send_batch_messages(
            tenant_id=10,
            actor_id=101,
            contact_ids=contact_ids,
            channel="email",
            connection_id=5,
            subject_template="Специальное предложение для {name} ({company})",
            body_template="Здравствуйте, {name}!\nДля компании {company} доступны новые условия.",
            purpose="marketing",
            idempotency_key="batch-2026-11-08-001",
            now=now,
            session=session,
        )

        assert batch["requested_count"] == 120
        assert batch["scheduled_count"] == 100
        assert batch["skipped_no_consent_count"] == 20
        assert len(batch["skipped_no_consent_contact_ids"]) == 20
        assert batch["skipped_conflict_count"] == 0
        assert batch["skipped_conflict_contact_ids"] == []
        assert batch["coverage_warning"] is not None
        assert "20 из 120" in batch["coverage_warning"]
        assert len(fake_outbox.draft_calls) == 100
        assert len(fake_outbox.schedule_calls) == 100

        # Check personalization on first scheduled draft
        first_draft_in = fake_outbox.draft_calls[0][0]
        assert first_draft_in.subject == "Специальное предложение для Клиент #1 (ООО Северсталь-Партнёр)"
        assert "Здравствуйте, Клиент #1!" in first_draft_in.body
        assert "Для компании ООО Северсталь-Партнёр доступны новые условия." in first_draft_in.body
        assert "https://crm.example.com/api/v1/integrations/bitrix/messaging/unsubscribe?token=" in first_draft_in.body
        assert "/stop" not in first_draft_in.body

        # Idempotent replay with same idempotency_key does not schedule additional messages
        replay = await svc.send_batch_messages(
            tenant_id=10,
            actor_id=101,
            contact_ids=contact_ids,
            channel="email",
            connection_id=5,
            subject_template="Специальное предложение для {name} ({company})",
            body_template="Здравствуйте, {name}!\nДля компании {company} доступны новые условия.",
            purpose="marketing",
            idempotency_key="batch-2026-11-08-001",
            now=now,
            session=session,
        )
        assert replay["batch_id"] == batch["batch_id"]
        assert len(fake_outbox.draft_calls) == 100

    # D16: Contact #1 clicks the one-click unsubscribe link (without any login / tenant context)
    first_scheduled_item = next(item for item in batch["items"] if item["contact_id"] == 1)
    unsub_url = first_scheduled_item["unsubscribe_url"]
    assert unsub_url.startswith("https://crm.example.com/api/v1/integrations/bitrix/messaging/unsubscribe?token=")
    token = unsub_url.split("token=", 1)[1]

    unsub_result = await svc.process_one_click_unsubscribe(
        token,
        now=now + timedelta(seconds=10),
        session=session,
    )
    assert unsub_result["unsubscribed"] is True
    assert unsub_result["contact_id"] == 1
    assert unsub_result["channel"] == "email"
    assert unsub_result["purpose"] == "marketing"
    assert unsub_result["cancelled_outbox_targets"] == 1

    # Verify Contact #1's scheduled outbox target was immediately cancelled and consent is now False
    with tenant_scope(10):
        assert (
            await has_active_consent(
                session,
                tenant_id=10,
                contact_id=1,
                channel="email",
                purpose="marketing",
                now=now + timedelta(seconds=15),
            )
            is False
        )
        status_after = await svc.get_batch_status(
            tenant_id=10,
            actor_id=101,
            batch_id=batch["batch_id"],
            session=session,
        )
        assert status_after["counts"]["cancelled"] == 1
        assert status_after["counts"]["approved"] == 99


@pytest.mark.asyncio
async def test_p14_t05_t06_real_outbox_store_consent_transitions_conflict_bucket_and_tenant_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real DB + real OutboxStore + real ContactManager test covering review blockers and medium items:
    1. Withdraw -> Grant -> Withdraw ordering through `ContactManager.record_consent` and `process_one_click_unsubscribe`.
    2. Year-scoped unsubscribe cancellation (`effective_now.year` only; prior-year 2025 rows stay untouched).
    3. Uniform 422 response (`invalid_unsubscribe_token`) for invalid tokens and deleted/merged contacts.
    4. Separate `skipped_conflict` bucket when `OutboxStore.draft` raises `OutboxConflict("outbox_idempotency_mismatch")`.
    5. Strict tenant isolation in `_resolve_contact_identity`.
    """
    session = SqliteMessagingSession()
    real_outbox = OutboxStore()
    contact_mgr = ContactManager()
    svc = CrmMessagingService(outbox=real_outbox)
    now = datetime.now(UTC)
    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", AsyncMock(return_value=SimpleNamespace(id=300)))

    owner_user = await session.get(User, 101)
    assert owner_user is not None

    # Tenant 10 contact #50 and Tenant 20 contact #60
    session.add(Contact(id=50, tenant_id=10, display_name_encrypted="ОльгаОрлова", preferred_channel="email"))
    session.add(
        ContactIdentity(
            id=501,
            tenant_id=10,
            contact_id=50,
            kind="email",
            value_encrypted="olga@example.com",
            blind_index="a" * 64,
            connection_id=5,
            verified=True,
        )
    )
    session.add(Contact(id=60, tenant_id=20, display_name_encrypted="Чужой Контакт", preferred_channel="email"))
    session.add(
        ContactIdentity(
            id=601,
            tenant_id=20,
            contact_id=60,
            kind="email",
            value_encrypted="foreign@example.com",
            blind_index="b" * 64,
            connection_id=5,
            verified=True,
        )
    )

    with tenant_scope(10), permission_scope(owner_user, member_grants=workspace_grants("owner")):
        # Tenant isolation in _resolve_contact_identity: tenant 20 identity #601 is never resolved under tenant 10
        isolated = await _resolve_contact_identity(
            session,
            tenant_id=10,
            contact_id=60,
            channel="email",
            connection_id=5,
            identity_id=601,
        )
        assert isolated is None

        # Blocker 5: Withdraw -> Grant -> Withdraw ordering via ContactManager.record_consent and unsubscribe
        t0 = now - timedelta(minutes=30)
        t1 = now - timedelta(minutes=20)
        t2 = now - timedelta(minutes=10)

        await contact_mgr.record_consent(
            50,
            "email",
            "marketing",
            "initial_withdraw",
            "manual:0",
            session=session,
            withdrawn_at=t0,
        )
        assert (
            await has_active_consent(
                session,
                tenant_id=10,
                contact_id=50,
                channel="email",
                purpose="marketing",
                now=now,
            )
            is False
        )

        # Grant after withdraw -> active consent is True
        await contact_mgr.record_consent(
            50,
            "email",
            "marketing",
            "re_opt_in",
            "web_form:1",
            session=session,
            granted_at=t1,
        )
        assert (
            await has_active_consent(
                session,
                tenant_id=10,
                contact_id=50,
                channel="email",
                purpose="marketing",
                now=now,
            )
            is True
        )

    # Withdraw again via one-click unsubscribe at t2 -> active consent becomes False
    tok_withdraw = create_unsubscribe_token(
        tenant_id=10,
        contact_id=50,
        channel="email",
        purpose="marketing",
        issued_at=int(t2.timestamp()),
    )
    await svc.process_one_click_unsubscribe(tok_withdraw, now=t2, session=session)
    with tenant_scope(10):
        assert (
            await has_active_consent(
                session,
                tenant_id=10,
                contact_id=50,
                channel="email",
                purpose="marketing",
                now=now,
            )
            is False
        )

    # Re-grant consent for 'greeting' and 'marketing' to test year-scoped cancellation and conflict bucket
    with tenant_scope(10), operator_permission_scope():
        await contact_mgr.record_consent(
            50,
            "email",
            "greeting",
            "opt_in",
            "form:greeting",
            session=session,
            granted_at=now - timedelta(minutes=5),
        )
        await contact_mgr.record_consent(
            50,
            "email",
            "marketing",
            "opt_in",
            "form:marketing",
            session=session,
            granted_at=now - timedelta(minutes=5),
        )

    # Medium 1: Seed 2025 and 2026 GreetingHistory and OutboxMessage rows for contact 50
    g_2025 = GreetingHistory(
        id=901,
        tenant_id=10,
        contact_id=50,
        event_type="birthday",
        event_date=date(2025, 11, 8),
        year=2025,
        channel="email",
        subject_encrypted="С днём рождения 2025",
        draft_text_encrypted="Поздравление 2025",
        status="approved",
    )
    g_2026 = GreetingHistory(
        id=902,
        tenant_id=10,
        contact_id=50,
        event_type="birthday",
        event_date=date(2026, 11, 8),
        year=2026,
        channel="email",
        subject_encrypted="С днём рождения 2026",
        draft_text_encrypted="Поздравление 2026",
        status="approved",
    )
    session.add(g_2025)
    session.add(g_2026)

    sched_2025 = datetime(2025, 11, 8, 10, 0, tzinfo=UTC)
    sched_2026 = datetime(2026, 11, 8, 10, 0, tzinfo=UTC)
    msg_2025 = OutboxMessage(
        id=801,
        tenant_id=10,
        author_id=101,
        contact_id=50,
        purpose="greeting",
        state="approved",
        timezone="UTC",
        subject_encrypted="2025",
        content_encrypted="Body 2025",
        content_hash="sha256:" + ("1" * 64),
        request_hash="sha256:" + ("1" * 64),
        idempotency_key="idem-2025",
        scheduled_at=sched_2025,
        approved_by=101,
        approved_hash="sha256:" + ("1" * 64),
        approval_expires_at=sched_2025 + timedelta(hours=24),
    )
    tgt_2025 = OutboxTarget(
        id=851,
        tenant_id=10,
        message_id=801,
        channel="email",
        connection_id=5,
        identity_id=501,
        recipient_encrypted="olga@example.com",
        credential_hash="sha256:" + ("1" * 64),
        content_hash="sha256:" + ("1" * 64),
        idempotency_key="1" * 64,
        state="approved",
        attempts=0,
        dispatch_after=sched_2025,
    )
    msg_2026 = OutboxMessage(
        id=802,
        tenant_id=10,
        author_id=101,
        contact_id=50,
        purpose="greeting",
        state="approved",
        timezone="UTC",
        subject_encrypted="2026",
        content_encrypted="Body 2026",
        content_hash="sha256:" + ("2" * 64),
        request_hash="sha256:" + ("2" * 64),
        idempotency_key="idem-2026",
        scheduled_at=sched_2026,
        approved_by=101,
        approved_hash="sha256:" + ("2" * 64),
        approval_expires_at=sched_2026 + timedelta(hours=24),
    )
    tgt_2026 = OutboxTarget(
        id=852,
        tenant_id=10,
        message_id=802,
        channel="email",
        connection_id=5,
        identity_id=501,
        recipient_encrypted="olga@example.com",
        credential_hash="sha256:" + ("2" * 64),
        content_hash="sha256:" + ("2" * 64),
        idempotency_key="2" * 64,
        state="approved",
        attempts=0,
        dispatch_after=sched_2026,
    )
    session.add(msg_2025)
    session.add(tgt_2025)
    session.add(msg_2026)
    session.add(tgt_2026)

    tok_greeting_2026 = create_unsubscribe_token(
        tenant_id=10,
        contact_id=50,
        channel="email",
        purpose="greeting",
        issued_at=int(datetime(2026, 11, 8, 12, 0, tzinfo=UTC).timestamp()),
    )
    unsub_2026 = await svc.process_one_click_unsubscribe(
        tok_greeting_2026,
        now=datetime(2026, 11, 8, 12, 5, tzinfo=UTC),
        session=session,
    )
    assert unsub_2026["cancelled_outbox_targets"] == 1
    assert session.greetings[902].status == "skipped"
    assert session.outbox_targets[852].state == "cancelled"
    # Historical 2025 greeting and outbox target remain untouched
    assert session.greetings[901].status == "approved"
    assert session.outbox_targets[851].state == "approved"

    # Medium 3: OutboxConflict("outbox_idempotency_mismatch") is placed in skipped_conflict, not skipped_no_route
    session.add(
        OutboxMessage(
            id=899,
            tenant_id=10,
            author_id=101,
            contact_id=50,
            purpose="marketing",
            state="validated",
            timezone="UTC",
            subject_encrypted="Old Subject",
            content_encrypted="Old Body",
            content_hash="sha256:" + ("9" * 64),
            request_hash="sha256:" + ("9" * 64),
            idempotency_key="batch-conflict-test:c50",
        )
    )
    with tenant_scope(10), permission_scope(owner_user, member_grants=workspace_grants("owner")):
        conflict_batch = await svc.send_batch_messages(
            tenant_id=10,
            actor_id=101,
            contact_ids=[50],
            channel="email",
            connection_id=5,
            subject_template="New Subject for {name}",
            body_template="New Body for {name}",
            purpose="marketing",
            idempotency_key="batch-conflict-test",
            now=now,
            session=session,
        )
        assert conflict_batch["scheduled_count"] == 0
        assert conflict_batch["skipped_no_route_count"] == 0
        assert conflict_batch["skipped_no_route_contact_ids"] == []
        assert conflict_batch["skipped_conflict_count"] == 1
        assert conflict_batch["skipped_conflict_contact_ids"] == [50]
        assert conflict_batch["items"][0]["reason"] == "outbox_idempotency_mismatch"

    # Medium 4: Uniform 422 response for invalid token AND valid token for non-existent/deleted contact
    tok_deleted_contact = create_unsubscribe_token(
        tenant_id=10,
        contact_id=999999,
        channel="email",
        purpose="marketing",
        issued_at=int(now.timestamp()),
    )
    with pytest.raises(Bitrix24Error) as exc_deleted:
        await svc.process_one_click_unsubscribe(tok_deleted_contact, now=now, session=session)
    with pytest.raises(Bitrix24Error) as exc_invalid:
        await svc.process_one_click_unsubscribe("invalid.token", now=now, session=session)

    assert exc_deleted.value.status_code == 422
    assert exc_invalid.value.status_code == 422
    assert exc_deleted.value.code == exc_invalid.value.code == "invalid_unsubscribe_token"
    assert str(exc_deleted.value) == str(exc_invalid.value) == "Invalid or expired unsubscribe token"


@pytest.mark.asyncio
async def test_p14_t06_d17_manual_consent_and_d16_public_http_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SqliteMessagingSession()
    now = datetime(2026, 11, 8, 15, 0, tzinfo=UTC)
    session.add(
        Contact(
            id=7,
            tenant_id=10,
            display_name_encrypted="Алексей Морозов",
            preferred_channel="telegram",
        )
    )

    audit_calls: list[dict[str, Any]] = []

    async def fake_record_success(**kwargs: Any) -> Any:
        audit_calls.append(kwargs)
        return SimpleNamespace(id=777)

    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", fake_record_success)

    svc = CrmMessagingService()
    manager_user = SimpleNamespace(
        id=42,
        is_active=True,
        username="manager",
        has_admin_access=lambda: False,
        role=SimpleNamespace(permissions=[]),
    )
    grants = frozenset(
        {
            ("source", ActionType.VIEW),
            ("contact", ActionType.VIEW),
            ("contact", ActionType.UPDATE),
        }
    )

    with tenant_scope(10), permission_scope(manager_user, member_grants=grants):
        consent_res = await svc.record_manual_consent(
            tenant_id=10,
            actor_id=manager_user.id,
            contact_id=7,
            channel="telegram",
            purpose="marketing",
            action="grant",
            basis="Подписанное письменное согласие клиента №14",
            source_evidence="scan://consents/2026-11-08/client-7.pdf",
            occurred_at=now,
            session=session,
        )
        assert consent_res["contact_id"] == 7
        assert consent_res["channel"] == "telegram"
        assert consent_res["purpose"] == "marketing"
        assert consent_res["action"] == "grant"
        assert consent_res["recorded_by_user_id"] == 42
        assert consent_res["audit_event_id"] == 777
        assert audit_calls[-1]["actor_kind"] == "user"
        assert audit_calls[-1]["actor_id"] == 42
        assert audit_calls[-1]["action"] == "crm.contact_update"
        assert audit_calls[-1]["resource_id"] == 7

        # Missing basis or source_evidence is rejected with 422
        with pytest.raises(Bitrix24Error) as exc_blank:
            await svc.record_manual_consent(
                tenant_id=10,
                actor_id=manager_user.id,
                contact_id=7,
                channel="telegram",
                purpose="marketing",
                action="grant",
                basis="   ",
                source_evidence="scan://consents/2026-11-08/client-7.pdf",
                occurred_at=now,
                session=session,
            )
        assert exc_blank.value.status_code == 422

    # Without contact.UPDATE permission, manual consent recording is rejected with 403
    viewer_grants = frozenset({("contact", ActionType.VIEW)})
    with tenant_scope(10), permission_scope(manager_user, member_grants=viewer_grants):
        with pytest.raises(Bitrix24Error) as exc_perm:
            await svc.record_manual_consent(
                tenant_id=10,
                actor_id=manager_user.id,
                contact_id=7,
                channel="telegram",
                purpose="marketing",
                action="grant",
                basis="basis",
                source_evidence="evidence",
                occurred_at=now,
                session=session,
            )
        assert exc_perm.value.status_code == 403

    # Verify public HTTP unsubscribe endpoint works through ApiScopeMiddleware without any Bearer token
    app = FastAPI()
    app.include_router(bitrix_endpoints.router, prefix="/api/v1/integrations/bitrix", dependencies=[])
    app.include_router(bitrix_endpoints.public_router, prefix="/api/v1/integrations/bitrix")
    wrapped_app = ApiScopeMiddleware(app)

    token = create_unsubscribe_token(
        tenant_id=10,
        contact_id=7,
        channel="telegram",
        purpose="marketing",
        issued_at=int(now.timestamp()),
    )

    async def fake_unsubscribe(tok: str) -> dict[str, Any]:
        return await svc.process_one_click_unsubscribe(tok, now=now + timedelta(minutes=1), session=session)

    monkeypatch.setattr(bitrix_endpoints.crm_messaging_service, "process_one_click_unsubscribe", fake_unsubscribe)

    async with AsyncClient(transport=ASGITransport(app=wrapped_app), base_url="http://testserver") as client:
        # GET without Authorization header succeeds (200)
        resp_get = await client.get(f"/api/v1/integrations/bitrix/messaging/unsubscribe?token={token}")
        assert resp_get.status_code == 200
        body_get = resp_get.json()
        assert body_get["unsubscribed"] is True
        assert body_get["contact_id"] == 7
        assert body_get["channel"] == "telegram"

        # POST without Authorization header succeeds (200)
        resp_post = await client.post(
            "/api/v1/integrations/bitrix/messaging/unsubscribe",
            json={"token": token},
        )
        assert resp_post.status_code == 200
        assert resp_post.json()["unsubscribed"] is True

        # Invalid token and valid token for deleted contact return identical HTTP 422 response
        deleted_tok = create_unsubscribe_token(
            tenant_id=10,
            contact_id=88888,
            channel="telegram",
            purpose="marketing",
            issued_at=int(now.timestamp()),
        )
        resp_bad = await client.post(
            "/api/v1/integrations/bitrix/messaging/unsubscribe",
            json={"token": "malformed-token"},
        )
        resp_deleted = await client.post(
            "/api/v1/integrations/bitrix/messaging/unsubscribe",
            json={"token": deleted_tok},
        )
        assert resp_bad.status_code == 422
        assert resp_deleted.status_code == 422
        bad_payload = {k: v for k, v in resp_bad.json().items() if k != "request_id"}
        deleted_payload = {k: v for k, v in resp_deleted.json().items() if k != "request_id"}
        assert bad_payload == deleted_payload

        # Protected endpoint (/api/v1/integrations/bitrix/messaging/batch) without Authorization header returns 401
        resp_protected = await client.post("/api/v1/integrations/bitrix/messaging/batch", json={})
        assert resp_protected.status_code == 401


@pytest.mark.asyncio
async def test_p14_t05_t06_greetings_delegate_and_authenticated_rest_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SqliteMessagingSession()
    fake_outbox = SqliteOutboxStoreAdapter(tenant_id=10, author_id=101)
    now = datetime(2026, 11, 8, 16, 0, tzinfo=UTC)
    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", AsyncMock(return_value=SimpleNamespace(id=501)))

    session.add(
        Contact(
            id=9,
            tenant_id=10,
            display_name_encrypted="Сергей Ковалёв",
            birthday_encrypted="1985-11-12",
            birthday_has_year=True,
            preferred_channel="telegram",
        )
    )
    session.add(
        ContactIdentity(
            id=91,
            tenant_id=10,
            contact_id=9,
            kind="telegram_id",
            value_encrypted="11223344",
            blind_index="9" * 64,
            connection_id=6,
            verified=True,
        )
    )
    session.add(
        ContactConsent(
            tenant_id=10,
            contact_id=9,
            channel="telegram",
            purpose="greeting",
            basis_encrypted="opt_in",
            source_evidence_encrypted="tg_start",
            granted_at=now - timedelta(days=2),
        )
    )

    greetings_svc = CrmGreetingsService()
    with tenant_scope(10):
        draft = await greetings_svc.generate_greeting_draft(
            tenant_id=10,
            actor_id=101,
            contact_id=9,
            event_type="birthday",
            year=2026,
            channel="telegram",
            reference_date=date(2026, 11, 8),
            session=session,
        )
        # Exercise CrmGreetingsService.send_greeting by (contact_id, event_type, year) without greeting_id
        sent = await greetings_svc.send_greeting(
            tenant_id=10,
            actor_id=101,
            connection_id=6,
            contact_id=9,
            event_type="birthday",
            year=2026,
            channel="telegram",
            outbox=fake_outbox,
            now=now,
            session=session,
        )
        assert sent["id"] == draft["id"]
        assert sent["status"] == "approved"

        # Exercise CrmGreetingsService.get_greeting_delivery_status when target is uncertain_delivery / failed
        tgt = session.outbox_targets[sent["outbox_target_id"]]
        tgt.attempts = 1
        tgt.claim_token = "claim-tok-002"
        tgt.claimed_at = now
        tgt.state = "uncertain_delivery"
        await session.flush()
        st_uncertain = await greetings_svc.get_greeting_delivery_status(
            tenant_id=10,
            actor_id=101,
            greeting_id=draft["id"],
            session=session,
        )
        assert st_uncertain["status"] == "unknown"

        tgt.attempts = 0
        tgt.claim_token = None
        tgt.claimed_at = None
        tgt.state = "approved"
        g_row = session.greetings[draft["id"]]
        g_row.status = "approved"
        await session.flush()

    # Unsubscribe from 'greeting' purpose marks the approved 2026 GreetingHistory row as 'skipped'
    unsub_token = sent["unsubscribe_url"].split("token=", 1)[1]
    messaging_svc = CrmMessagingService(outbox=fake_outbox)
    unsub_out = await messaging_svc.process_one_click_unsubscribe(
        unsub_token,
        now=now + timedelta(minutes=2),
        session=session,
    )
    assert unsub_out["purpose"] == "greeting"
    assert session.greetings[draft["id"]].status == "skipped"

    # Exercise authenticated REST endpoints in app/api/v1/endpoints/bitrix.py
    app = FastAPI()
    app.include_router(bitrix_endpoints.router, prefix="/api/v1/integrations/bitrix")
    actor = SimpleNamespace(
        id=101,
        is_active=True,
        username="owner",
        has_admin_access=lambda: False,
        role=SimpleNamespace(permissions=[]),
    )
    app.dependency_overrides[get_authenticated_user] = lambda: actor
    monkeypatch.setattr("app.api.deps._load_with_rights", AsyncMock(return_value=actor))

    @app.middleware("http")
    async def _bind_scope(request: Any, call_next: Any) -> Any:
        request.state.api_user = actor
        request.state.tenant_id = 10
        grants = frozenset(
            {
                ("source", ActionType.VIEW),
                ("source", ActionType.CREATE),
                ("contact", ActionType.VIEW),
                ("contact", ActionType.UPDATE),
                ("outboxmessage", ActionType.VIEW),
                ("outboxmessage", ActionType.CONFIGURE),
            }
        )
        with tenant_scope(10), permission_scope(actor, member_grants=grants):
            return await call_next(request)

    monkeypatch.setattr(
        bitrix_endpoints.crm_greetings_service,
        "send_greeting",
        AsyncMock(return_value=sent),
    )
    monkeypatch.setattr(
        bitrix_endpoints.crm_greetings_service,
        "get_greeting_delivery_status",
        AsyncMock(
            return_value={
                "id": draft["id"],
                "tenant_id": 10,
                "contact_id": 9,
                "event_type": "birthday",
                "event_date": date(2026, 11, 12),
                "year": 2026,
                "channel": "telegram",
                "status": "sent",
                "command_id": sent["command_id"],
                "sent_at": now,
                "outbox_message_id": sent["outbox_message_id"],
                "outbox_target_id": sent["outbox_target_id"],
                "outbox_state": "sent",
                "provider_message_id": "tg-msg-1",
                "error_code": None,
            }
        ),
    )
    fake_batch_out = {
        "batch_id": 88,
        "tenant_id": 10,
        "channel": "telegram",
        "purpose": "marketing",
        "state": "approved",
        "requested_count": 2,
        "scheduled_count": 1,
        "skipped_no_consent_count": 1,
        "skipped_no_consent_contact_ids": [10],
        "skipped_no_route_count": 0,
        "skipped_no_route_contact_ids": [],
        "skipped_conflict_count": 0,
        "skipped_conflict_contact_ids": [],
        "coverage_warning": "Пропущено контактов без подтверждённого согласия (opt-in D15, цель 'marketing'): 1 из 2.",
        "counts": {"approved": 1, "skipped": 1},
        "items": [
            {
                "contact_id": 9,
                "status": "approved",
                "reason": None,
                "outbox_message_id": 1,
                "outbox_target_id": 1,
                "command_id": 1,
                "unsubscribe_url": sent["unsubscribe_url"],
            }
        ],
    }
    monkeypatch.setattr(
        bitrix_endpoints.crm_messaging_service,
        "send_batch_messages",
        AsyncMock(return_value=fake_batch_out),
    )
    monkeypatch.setattr(
        bitrix_endpoints.crm_messaging_service,
        "get_batch_status",
        AsyncMock(return_value=fake_batch_out),
    )
    monkeypatch.setattr(
        bitrix_endpoints.crm_messaging_service,
        "record_manual_consent",
        AsyncMock(
            return_value={
                "id": 301,
                "tenant_id": 10,
                "contact_id": 9,
                "channel": "telegram",
                "purpose": "marketing",
                "action": "grant",
                "granted_at": now,
                "withdrawn_at": None,
                "recorded_by_user_id": 101,
                "audit_event_id": 501,
            }
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        r_send = await client.post(
            "/api/v1/integrations/bitrix/greetings/send",
            json={"greeting_id": draft["id"], "connection_id": 6, "channel": "telegram"},
        )
        assert r_send.status_code == 200
        assert r_send.json()["status"] == "approved"

        r_gstatus = await client.get(f"/api/v1/integrations/bitrix/greetings/{draft['id']}/status")
        assert r_gstatus.status_code == 200
        assert r_gstatus.json()["status"] == "sent"

        r_batch = await client.post(
            "/api/v1/integrations/bitrix/messaging/batch",
            json={
                "contact_ids": [9, 10],
                "channel": "telegram",
                "connection_id": 6,
                "body_template": "Привет, {name}!",
                "purpose": "marketing",
                "idempotency_key": "batch-rest-001",
            },
        )
        assert r_batch.status_code == 202
        assert r_batch.json()["batch_id"] == 88
        assert r_batch.json()["skipped_no_consent_count"] == 1
        assert r_batch.json()["skipped_conflict_count"] == 0

        r_bstatus = await client.get("/api/v1/integrations/bitrix/messaging/status/88")
        assert r_bstatus.status_code == 200
        assert r_bstatus.json()["scheduled_count"] == 1

        r_consent = await client.post(
            "/api/v1/integrations/bitrix/contacts/9/consents",
            json={
                "channel": "telegram",
                "purpose": "marketing",
                "action": "grant",
                "basis": "Письменное согласие",
                "source_evidence": "doc://consent-9",
            },
        )
        assert r_consent.status_code == 201
        assert r_consent.json()["recorded_by_user_id"] == 101
