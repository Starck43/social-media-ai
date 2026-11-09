"""Tenant/actor fenced calendar orchestration with source-owned schedules.

Locks follow the planner actor -> workspace -> connection -> event order. Provider
writes use the existing command ledger with a committed, non-retryable claim.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_maker, with_db_session
from app.integrations.calendar.caldav import YandexCalendar
from app.integrations.calendar.google import GoogleCalendar
from app.integrations.oauth.service import OAuthFlowError, WorkspaceOAuthService, decode_secret_bundle
from app.models import ActionAuditEvent, BusinessCommand, Tenant, WorkspaceConnection, WorkspaceConnectionSecret
from app.models.actions.business_command import BusinessCommandTarget
from app.models.calendar import CalendarChoice, CalendarEvent, SourceObject
from app.services.calendar.contracts import CalendarError, EventInput, PreconditionFailed, ReauthRequired
from app.services.commands.ledger import (
    BusinessCommandLedger,
    CommandDenied,
    _is_capability_enabled,
    _verify_member_grant,
)
from app.services.commands.registry import canonical_payload_hash, get_capability
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable, planner_service
from app.types import ActionType
from app.utils.crypto import decrypt_secret, encrypt_secret

PROVIDERS = ("google_calendar", "yandex_calendar")
CAPABILITY = "calendar.event_write.v1"


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def event_view(row):
    return {
        "id": row.id,
        "task_id": row.task_id,
        "state": row.state,
        "origin": row.origin,
        "version": row.version,
        "conflict": json.loads(row.conflict_encrypted) if row.conflict_encrypted else None,
    }


class CalendarService:
    def __init__(self, *, adapter_factory=None, oauth=None):
        self.adapter_factory = adapter_factory
        self.oauth = oauth or WorkspaceOAuthService()
        self.ledger = BusinessCommandLedger()

    async def _connection(self, session, connection_id, identity, *, verified=True):
        row = await session.scalar(
            select(WorkspaceConnection)
            .where(
                WorkspaceConnection.tenant_id == identity[0],
                WorkspaceConnection.owner_user_id == identity[1],
                WorkspaceConnection.id == connection_id,
                WorkspaceConnection.provider_id.in_(PROVIDERS),
            )
            .with_for_update()
        )
        if row is None:
            raise PlannerUnavailable("calendar_unavailable")
        if verified and (not row.is_active or row.state != "verified"):
            raise CalendarError("source_unavailable")
        return row

    async def _adapter(self, session, conn, identity):
        if self.adapter_factory is not None:
            return self.adapter_factory(conn)
        if conn.provider_id == "google_calendar":
            try:
                await self.oauth.refresh_tokens(
                    tenant_id=identity[0],
                    actor_id=identity[1],
                    connection_id=conn.id,
                    method_id=conn.auth_method,
                    session=session,
                )
            except OAuthFlowError as exc:
                if exc.code == "oauth_reauth_required":
                    conn.state = "reauth"
                    raise ReauthRequired() from None
                raise CalendarError() from None
        secret = await session.get(WorkspaceConnectionSecret, conn.id)
        if secret is None or not secret.secret_encrypted:
            conn.state = "reauth"
            raise ReauthRequired()
        if conn.provider_id == "google_calendar":
            bundle = decode_secret_bundle(secret.secret_encrypted)
            return GoogleCalendar(bundle["access_token"], conn.config["calendar_id"])
        bundle = json.loads(decrypt_secret(secret.secret_encrypted))
        return YandexCalendar(bundle["login"], bundle["password"], conn.config["calendar_id"])

    async def _choice(self, session, identity):
        row = await session.scalar(
            select(CalendarChoice)
            .where(CalendarChoice.tenant_id == identity[0], CalendarChoice.owner_user_id == identity[1])
            .with_for_update()
        )
        if row is None:
            row = CalendarChoice(tenant_id=identity[0], owner_user_id=identity[1])
            session.add(row)
            await session.flush()
        return row

    @with_db_session
    async def connect_google(self, request, session):
        identity = await planner_service._actor(session, ActionType.CREATE)
        base = settings.PUBLIC_BASE_URL.rstrip("/")
        if not base.startswith("https://"):
            raise CalendarError("calendar_oauth_not_configured")
        if request.connection_id is not None:
            conn = await self._connection(session, request.connection_id, identity, verified=False)
            if conn.provider_id != "google_calendar" or conn.config["calendar_id"] != request.calendar_id:
                raise PlannerConflict("calendar_identity_immutable")
        # The callback remains authenticated and binds state to actor/workspace.
        result = await self.oauth.start_authorization(
            tenant_id=identity[0],
            actor_id=identity[1],
            provider_id="google_calendar",
            method_id="oauth2_readonly" if request.read_only else "oauth2",
            connection_id=request.connection_id,
            account_key="calendar:" + uuid.uuid4().hex,
            config_overrides={
                "calendar_id": request.calendar_id,
                "read_only": request.read_only,
                "write_consent": request.write_consent,
                "consented_by": identity[1],
            },
            redirect_uri=base + "/app/settings/oauth/google_calendar/callback",
            session=session,
        )
        conn = await self._connection(session, result["connection_id"], identity, verified=False)
        conn.config = {**conn.config, "oauth_state_hash": digest(result["state"])}
        await self._choice(session, identity)  # Never choose by creation order.
        return {
            "connection_id": result["connection_id"],
            "authorize_url": result["authorize_url"],
            "state": result["state"],
        }

    @with_db_session
    async def callback_google(self, request, session):
        identity = await planner_service._actor(session, ActionType.CREATE)
        connection_id = request.connection_id
        if connection_id is None:
            connection_id = await session.scalar(
                select(WorkspaceConnection.id).where(
                    WorkspaceConnection.tenant_id == identity[0],
                    WorkspaceConnection.owner_user_id == identity[1],
                    WorkspaceConnection.provider_id == "google_calendar",
                    WorkspaceConnection.config["oauth_state_hash"].as_string() == digest(request.state),
                )
            )
        conn = await self._connection(session, connection_id, identity, verified=False)
        if conn.provider_id != "google_calendar":
            raise PermissionError("calendar_denied")
        await self.oauth.complete_callback(
            tenant_id=identity[0],
            actor_id=identity[1],
            provider_id=conn.provider_id,
            method_id=conn.auth_method,
            state_token=request.state,
            code=request.code,
            expected_connection_id=conn.id,
            session=session,
        )
        return {"connection_id": conn.id, "state": conn.state}

    @with_db_session
    async def discover_yandex(self, request, session):
        await planner_service._actor(session, ActionType.CREATE)
        adapter = YandexCalendar(request.login, request.app_password, "/")
        return {"items": await adapter.discover()}

    @with_db_session
    async def connect_yandex(self, request, session):
        identity = await planner_service._actor(session, ActionType.CREATE)
        adapter = YandexCalendar(request.login, request.app_password, request.calendar_id)
        calendars = await adapter.discover()
        if adapter.path not in {item["id"] for item in calendars}:
            raise PlannerUnavailable("calendar_unavailable")
        if request.connection_id is not None:
            conn = await self._connection(session, request.connection_id, identity, verified=False)
            if conn.provider_id != "yandex_calendar" or conn.config["calendar_id"] != adapter.path:
                raise PlannerConflict("calendar_identity_immutable")
            conn.is_active, conn.state = True, "verified"
            conn.config = {
                **conn.config,
                "read_only": request.read_only,
                "write_consent": request.write_consent,
                "consented_by": identity[1],
            }
            secret = await session.get(WorkspaceConnectionSecret, conn.id)
            if secret is None:
                secret = WorkspaceConnectionSecret(connection_id=conn.id)
                session.add(secret)
            secret.secret_encrypted = encrypt_secret(
                encoded({"login": request.login, "password": request.app_password})
            )
            return {"connection_id": conn.id, "state": conn.state}
        conn = WorkspaceConnection(
            tenant_id=identity[0],
            owner_user_id=identity[1],
            platform="calendar",
            provider_id="yandex_calendar",
            auth_method="app_password",
            schema_version=1,
            label="Yandex Calendar",
            account_key="calendar:" + uuid.uuid4().hex,
            is_active=True,
            state="verified",
            config={
                "calendar_id": adapter.path,
                "read_only": request.read_only,
                "write_consent": request.write_consent,
                "consented_by": identity[1],
            },
        )
        session.add(conn)
        await session.flush()
        session.add(
            WorkspaceConnectionSecret(
                connection_id=conn.id,
                secret_encrypted=encrypt_secret(encoded({"login": request.login, "password": request.app_password})),
            )
        )
        await self._choice(session, identity)
        return {"connection_id": conn.id, "state": conn.state}

    @with_db_session
    async def connections(self, session):
        identity = await planner_service._actor(session, ActionType.VIEW)
        rows = list(
            (
                await session.scalars(
                    select(WorkspaceConnection)
                    .where(
                        WorkspaceConnection.tenant_id == identity[0],
                        WorkspaceConnection.owner_user_id == identity[1],
                        WorkspaceConnection.provider_id.in_(PROVIDERS),
                        WorkspaceConnection.is_active.is_(True),
                    )
                    .order_by(WorkspaceConnection.id)
                )
            ).all()
        )
        choice = await session.scalar(
            select(CalendarChoice).where(
                CalendarChoice.tenant_id == identity[0], CalendarChoice.owner_user_id == identity[1]
            )
        )
        selected = choice.connection_id if choice is not None else None
        if selected not in {r.id for r in rows}:
            selected = None
        return {
            "items": [
                {
                    "id": r.id,
                    "provider": r.provider_id,
                    "state": r.state,
                    "calendar_id": r.config["calendar_id"],
                    "read_only": r.config["read_only"],
                }
                for r in rows
            ],
            "selected_connection_id": selected,
            "choice_required": bool(rows) and selected is None,
        }

    @with_db_session
    async def choose(self, connection_id, session):
        identity = await planner_service._actor(session, ActionType.UPDATE)
        conn = await self._connection(session, connection_id, identity)
        choice = await self._choice(session, identity)
        choice.connection_id = conn.id
        return {"selected_connection_id": conn.id}

    @with_db_session
    async def disconnect(self, connection_id, session):
        identity = await planner_service._actor(session, ActionType.DELETE)
        conn = await self._connection(session, connection_id, identity, verified=False)
        conn.is_active, conn.state = False, "disabled"
        secret = await session.get(WorkspaceConnectionSecret, conn.id)
        if secret is not None:
            secret.secret_encrypted = ""
        choice = await self._choice(session, identity)
        if choice.connection_id == conn.id:
            choice.connection_id = None
        return {"connection_id": conn.id, "state": conn.state}

    async def _event(self, session, event_id, conn):
        row = await session.scalar(
            select(CalendarEvent)
            .join(SourceObject, CalendarEvent.source_object_id == SourceObject.id)
            .where(
                CalendarEvent.tenant_id == conn.tenant_id,
                CalendarEvent.id == event_id,
                SourceObject.tenant_id == conn.tenant_id,
                SourceObject.connection_id == conn.id,
            )
            .with_for_update(of=CalendarEvent)
        )
        if row is None:
            raise PlannerUnavailable("calendar_unavailable")
        source = await session.scalar(
            select(SourceObject).where(
                SourceObject.tenant_id == conn.tenant_id, SourceObject.id == row.source_object_id
            )
        )
        return row, source

    async def _audit(self, session, event, identity, operation="event_update"):
        await ActionAuditEvent.objects.record_success(
            session=session,
            actor_kind="user",
            actor_id=identity[1],
            action="calendar." + operation,
            resource_kind="event",
            resource_id=event.id,
        )

    @staticmethod
    def _conflict(row, remote, task=None):
        row.state = "conflict"
        # Only exceptional evidence is retained locally, encrypted at rest.
        row.conflict_encrypted = encoded(
            {
                "remote": remote,
                "local": json.loads(row.proposal_encrypted) if row.proposal_encrypted else None,
                "task_version": None if task is None else task.version,
                "planner": (
                    None
                    if task is None
                    else {
                        "title": task.title_encrypted,
                        "description": task.description_encrypted,
                        "due_at": task.due_at.isoformat() if task.due_at else None,
                        "due_date": task.due_date.isoformat() if task.due_date else None,
                        "timezone": task.timezone,
                        "recurrence": task.recurrence,
                    }
                ),
            }
        )
        row.version += 1

    @with_db_session
    async def sync(self, connection_id, session):
        identity = await planner_service._actor(session, ActionType.UPDATE)
        conn = await self._connection(session, connection_id, identity)
        try:
            adapter = await self._adapter(session, conn, identity)
            items, cursor = await adapter.pull(conn.config.get("cursor"))
        except ReauthRequired:
            conn.state = "reauth"
            return {"state": "reauth", "events": 0}
        except CalendarError:
            # Never empty the calendar or advance the cursor on partial failure.
            return {"state": "source_unavailable", "events": 0}
        count = 0
        for item in items:
            source = await session.scalar(
                select(SourceObject).where(
                    SourceObject.tenant_id == identity[0],
                    SourceObject.connection_id == conn.id,
                    SourceObject.external_id == item["external_id"],
                )
            )
            if source is None:
                source = SourceObject(tenant_id=identity[0], connection_id=conn.id, external_id=item["external_id"])
                session.add(source)
                await session.flush()
            row = await session.scalar(
                select(CalendarEvent)
                .where(CalendarEvent.tenant_id == identity[0], CalendarEvent.source_object_id == source.id)
                .with_for_update()
            )
            if row is None:
                row = CalendarEvent(
                    tenant_id=identity[0], source_object_id=source.id, origin="external", state="synced", version=1
                )
                session.add(row)
                await session.flush()
            task = (
                None if row.task_id is None else await planner_service._task(session, row.task_id, identity, lock=True)
            )
            local_changed = row.proposal_encrypted is not None or (
                task is not None and task.version != row.task_version
            )
            if row.state == "unknown":
                # A provider observation is not authorization to resend an unknown command.
                continue
            if item["deleted"] or (row.etag is not None and row.etag != item["etag"] and local_changed):
                self._conflict(row, item, task)
            elif row.state != "conflict":
                row.etag = item["etag"]
                row.task_version = None if task is None else task.version
                row.version += 1
            await self._audit(session, row, identity)
            count += 1
        conn.config = {**conn.config, "cursor": cursor, "last_sync": datetime.now(timezone.utc).isoformat()}
        return {"state": "synced", "events": count}

    @with_db_session
    async def events(self, session, *, connection_id=None, limit=50, before_id=None):
        identity = await planner_service._actor(session, ActionType.VIEW)
        if connection_id is None:
            choice = await session.scalar(
                select(CalendarChoice).where(
                    CalendarChoice.tenant_id == identity[0], CalendarChoice.owner_user_id == identity[1]
                )
            )
            if choice is None or choice.connection_id is None:
                raise PlannerConflict("calendar_choice_required")
            connection_id = choice.connection_id
        conn = await self._connection(session, connection_id, identity)
        query = (
            select(CalendarEvent)
            .join(SourceObject, CalendarEvent.source_object_id == SourceObject.id)
            .where(
                CalendarEvent.tenant_id == identity[0],
                SourceObject.tenant_id == identity[0],
                SourceObject.connection_id == conn.id,
            )
        )
        if before_id is not None:
            query = query.where(CalendarEvent.id < before_id)
        rows = list((await session.scalars(query.order_by(CalendarEvent.id.desc()).limit(limit + 1))).all())
        try:
            adapter = await self._adapter(session, conn, identity)
            if not rows and before_id is None:
                # An empty identity inventory is not proof of an empty source.
                # Confirm availability before displaying an empty calendar; a
                # non-empty provider requires discovery sync, not a false empty.
                discovered, _ = await adapter.pull(None)
                if any(not item["deleted"] for item in discovered):
                    return {"state": "sync_required", "items": None, "next_before_id": None}
            result = []
            for row in rows[:limit]:
                _, source = await self._event(session, row.id, conn)
                try:
                    remote = await adapter.read(source.external_id)
                except CalendarError as exc:
                    if exc.code != "event_missing":
                        raise
                    remote = {"deleted": True}
                result.append({**event_view(row), "source": remote})
            return {
                "state": "available",
                "items": result,
                "next_before_id": rows[limit - 1].id if len(rows) > limit else None,
            }
        except ReauthRequired:
            conn.state = "reauth"
            return {"state": "reauth", "items": None, "next_before_id": None}
        except CalendarError:
            return {"state": "source_unavailable", "items": None, "next_before_id": None}

    @staticmethod
    def _writable(conn, identity):
        if (
            conn.config.get("read_only", True)
            or conn.config.get("write_consent") is not True
            or conn.config.get("consented_by") != identity[1]
        ):
            raise PermissionError("calendar_consent_required")

    @with_db_session
    async def preview(self, request, idempotency_key, session):
        identity = await planner_service._actor(session, ActionType.UPDATE)
        conn = await self._connection(session, request.connection_id, identity)
        if request.operation != "resolve_remote":
            self._writable(conn, identity)
        choice = await self._choice(session, identity)
        if choice.connection_id != conn.id:
            raise PlannerConflict("calendar_choice_required")
        # Serializing on the workspace makes idempotency lookup/creation atomic.
        existing = await session.scalar(
            select(BusinessCommand).where(
                BusinessCommand.tenant_id == identity[0],
                BusinessCommand.actor_user_id == identity[1],
                BusinessCommand.capability == CAPABILITY,
                BusinessCommand.idempotency_key == idempotency_key,
            )
        )
        fingerprint = digest(request.model_dump(mode="json"))
        if existing is not None:
            if existing.payload.get("request_hash") != fingerprint:
                raise PlannerConflict("calendar_idempotency_conflict")
            return await self.ledger.get_command(existing.id, session=session)
        task = (
            None
            if request.task_id is None
            else await planner_service._task(session, request.task_id, identity, lock=True)
        )
        if request.event_id is None:
            linked = await session.scalar(
                select(CalendarEvent.id).where(CalendarEvent.tenant_id == identity[0], CalendarEvent.task_id == task.id)
            )
            if linked is not None:
                raise PlannerConflict("calendar_task_already_linked")
            source = SourceObject(
                tenant_id=identity[0],
                connection_id=conn.id,
                external_id=(
                    uuid.uuid4().hex
                    if conn.provider_id == "google_calendar"
                    else conn.config["calendar_id"] + uuid.uuid4().hex + ".ics"
                ),
            )
            session.add(source)
            await session.flush()
            row = CalendarEvent(
                tenant_id=identity[0],
                source_object_id=source.id,
                task_id=task.id,
                origin="local",
                state="pending",
                task_version=task.version,
                version=1,
            )
            session.add(row)
            await session.flush()
        else:
            row, source = await self._event(session, request.event_id, conn)
            if task is not None and task.id != row.task_id:
                raise PlannerConflict("calendar_task_binding")
            task = (
                None if row.task_id is None else await planner_service._task(session, row.task_id, identity, lock=True)
            )
        if row.state == "unknown" and request.operation == "resolve_remote":
            # Recovery is an explicitly approved read/accept decision, never a
            # retry of the uncertain provider write. Retain its old command.
            adapter = await self._adapter(session, conn, identity)
            try:
                observed = await adapter.read(source.external_id)
            except CalendarError as exc:
                if exc.code != "event_missing":
                    raise
                observed = {"deleted": True, "etag": None, "payload": {}}
            self._conflict(row, observed, task)
        if row.state in {"unknown", "deleted"}:
            raise PlannerConflict("calendar_event_not_editable")
        if row.state == "conflict" and request.operation not in {"resolve_local", "resolve_remote"}:
            raise PlannerConflict("calendar_resolution_required")
        if row.state != "conflict" and request.operation in {"resolve_local", "resolve_remote"}:
            raise PlannerConflict("calendar_no_conflict")
        row.proposal_encrypted = request.event.model_dump_json() if request.event else None
        row.version += 1
        payload = {
            "event_id": row.id,
            "version": row.version,
            "request_hash": fingerprint,
            "operation": request.operation,
            "event_hash": digest(json.loads(row.proposal_encrypted)) if row.proposal_encrypted else None,
            "task_version": None if task is None else task.version,
            "etag": row.etag,
        }
        if row.state == "conflict":
            payload["etag"] = json.loads(row.conflict_encrypted)["remote"].get("etag")
        command = await self.ledger.preview_command(
            capability=CAPABILITY,
            idempotency_key=idempotency_key,
            target_refs=["calendar:" + str(row.id)],
            payload=payload,
            connection_id=conn.id,
            preview_snapshot={
                "event_id": row.id,
                "operation": request.operation,
                "per_item_approval_required": request.operation != "upsert",
            },
            session=session,
        )
        if request.operation != "upsert":
            item_command = await session.get(BusinessCommand, command["id"])
            item_command.approval_policy = "per_item"
            command["approval_policy"] = "per_item"
        if request.operation == "upsert":
            command = await self.ledger.approve_command(
                command["id"], expected_payload_hash=command["payload_hash"], session=session
            )
        return command

    async def execute(self, command_id):
        """Claim durably before I/O; a claimed/unknown command is never sent twice.

        No caller-provided adapter, capability or raw event payload can reach I/O.
        An interrupted final receipt requires reconciliation, not automatic retry.
        """
        async with async_session_maker() as session, session.begin():
            identity = await planner_service._actor(session, ActionType.UPDATE)
            cmd = await session.scalar(
                select(BusinessCommand)
                .where(
                    BusinessCommand.tenant_id == identity[0],
                    BusinessCommand.id == command_id,
                    BusinessCommand.actor_user_id == identity[1],
                )
                .with_for_update()
            )
            if cmd is None or cmd.capability != CAPABILITY or cmd.state != "approved":
                raise PlannerConflict("calendar_command_unavailable")
            spec = get_capability(CAPABILITY)
            if cmd.payload.get("operation") != "resolve_remote" and not _is_capability_enabled(spec):
                raise CommandDenied("calendar_capability_disabled")
            await _verify_member_grant(session, identity[0], identity[1], spec)
            await _verify_member_grant(session, identity[0], cmd.approved_user_id, spec)
            now = datetime.now(timezone.utc)
            actual = canonical_payload_hash(cmd.capability, list(cmd.target_refs), dict(cmd.payload))
            if (
                actual != cmd.payload_hash
                or actual != cmd.approved_payload_hash
                or cmd.approval_expires_at is None
                or cmd.approval_expires_at <= now
            ):
                raise CommandDenied("calendar_approval_invalid")
            conn = await self._connection(session, cmd.connection_id, identity)
            if cmd.payload.get("operation") != "resolve_remote":
                self._writable(conn, identity)
            choice = await self._choice(session, identity)
            if choice.connection_id != conn.id:
                raise PlannerConflict("calendar_choice_changed")
            row, source = await self._event(session, cmd.payload["event_id"], conn)
            if row.version != cmd.payload["version"] or row.state in {"unknown", "deleted"}:
                raise PlannerConflict("calendar_stale_preview")
            task = (
                None if row.task_id is None else await planner_service._task(session, row.task_id, identity, lock=True)
            )
            if (None if task is None else task.version) != cmd.payload["task_version"]:
                raise PlannerConflict("calendar_stale_task")
            value = json.loads(row.proposal_encrypted) if row.proposal_encrypted else None
            if (digest(value) if value else None) != cmd.payload["event_hash"]:
                raise PlannerConflict("calendar_stale_payload")
            try:
                adapter = await self._adapter(session, conn, identity)
            except ReauthRequired:
                conn.state = "reauth"
                return {"state": "reauth", "command_id": cmd.id}
            operation, external_id, etag = cmd.payload["operation"], source.external_id, cmd.payload["etag"]
            if operation not in {"upsert", "delete", "resolve_local", "resolve_remote"}:
                raise CommandDenied("calendar_operation_invalid")
            event_id, connection_id = row.id, conn.id
            desired = EventInput.model_validate(value) if value else None
            cmd.state, cmd.claimed_at = "claimed", now
            row.state = "unknown"
            targets = list(
                (
                    await session.scalars(
                        select(BusinessCommandTarget).where(
                            BusinessCommandTarget.tenant_id == identity[0], BusinessCommandTarget.command_id == cmd.id
                        )
                    )
                ).all()
            )
            for target in targets:
                target.state = "claimed"
        # The claim transaction above has committed. Any crash leaves a visible
        # claimed command and unknown event, blocking duplicate delivery.
        remote, error = None, None
        try:
            if operation == "resolve_remote":
                try:
                    remote = await adapter.read(external_id)
                except CalendarError as exc:
                    if exc.code != "event_missing":
                        raise
                    remote = {"deleted": True, "etag": etag, "payload": {}}
                if remote.get("etag") != etag:
                    raise PreconditionFailed()
            elif operation == "delete":
                await adapter.delete(external_id, etag)
                remote = {"deleted": True, "etag": etag, "payload": {}}
            else:
                remote = await adapter.write(external_id, desired, etag)
        except PreconditionFailed:
            error = "calendar_conflict"
            try:
                remote = await adapter.read(external_id)
            except CalendarError:
                remote = {"external_id": external_id, "etag": etag, "source_unavailable": True}
        except ReauthRequired:
            error = "reauth"
        except (CalendarError, TimeoutError, OSError):
            error = "outcome_unconfirmed"
        async with async_session_maker() as session, session.begin():
            # Serialize receipt writes with sync/planner operations without
            # requiring an actor who may have been revoked after dispatch.
            await session.scalar(select(Tenant.id).where(Tenant.id == identity[0]).with_for_update())
            # Receipt persistence must survive actor revocation after dispatch.
            cmd = await session.scalar(
                select(BusinessCommand)
                .where(BusinessCommand.tenant_id == identity[0], BusinessCommand.id == command_id)
                .with_for_update()
            )
            conn = await session.scalar(
                select(WorkspaceConnection).where(
                    WorkspaceConnection.tenant_id == identity[0], WorkspaceConnection.id == connection_id
                )
            )
            row, _ = await self._event(session, event_id, conn)
            targets = list(
                (
                    await session.scalars(
                        select(BusinessCommandTarget).where(
                            BusinessCommandTarget.tenant_id == identity[0], BusinessCommandTarget.command_id == cmd.id
                        )
                    )
                ).all()
            )
            cmd.executed_at, cmd.error_code = datetime.now(timezone.utc), error
            if error == "calendar_conflict":
                cmd.state = "failed"
                self._conflict(row, remote)
            elif error:
                cmd.state = "unknown"
                row.state = "unknown"
                cmd.reconcile_hint = "Read provider event by the bound identity; never resend this command."
                if error == "reauth":
                    conn.state = "reauth"
            else:
                cmd.state = "succeeded"
                row.state = "deleted" if remote.get("deleted") else "synced"
                concurrent_local_change = False
                if task is not None:
                    # Preserve external recurrence authority even for non-recurring
                    # events; future schedule changes must go to this source.
                    task = await session.scalar(
                        select(type(task))
                        .where(type(task).tenant_id == identity[0], type(task).id == task.id)
                        .with_for_update()
                    )
                    if task is not None:
                        task.recurrence_source = "calendar"
                        concurrent_local_change = task.version != cmd.payload["task_version"]
                        if concurrent_local_change:
                            self._conflict(row, remote, task)
                        else:
                            row.task_version = task.version
                if not concurrent_local_change:
                    row.etag, row.conflict_encrypted, row.proposal_encrypted = remote.get("etag"), None, None
                row.version += 1
                await self._audit(session, row, identity, "event_delete" if operation == "delete" else "event_update")
            for target in targets:
                target.state, target.error_code = cmd.state, error
                target.executed_at = cmd.executed_at
                target.provider_receipt = None if error else str(row.id)
            return {"state": cmd.state, "command_id": cmd.id, "event_id": row.id, "error_code": error}


calendar_service = CalendarService()


async def authoritative_task_projection(task, projection, session, identity):
    """Read-time schedule for a linked task; no stale local fallback on failure."""
    if task.recurrence_source != "calendar":
        return projection
    source = await session.scalar(
        select(SourceObject)
        .join(CalendarEvent, CalendarEvent.source_object_id == SourceObject.id)
        .where(
            SourceObject.tenant_id == identity[0],
            CalendarEvent.tenant_id == identity[0],
            CalendarEvent.task_id == task.id,
            CalendarEvent.state != "deleted",
        )
    )
    if source is None:
        raise PlannerConflict("planner_source_unavailable")
    try:
        conn = await calendar_service._connection(session, source.connection_id, identity)
        adapter = await calendar_service._adapter(session, conn, identity)
        remote = await adapter.read(source.external_id)
        if remote.get("deleted"):
            raise CalendarError()
        if conn.provider_id == "google_calendar":
            payload = remote["payload"]
            start = payload["start"]
            recurrence = payload.get("recurrence", [])
            values = {
                "due_at": datetime.fromisoformat(start["dateTime"]) if "dateTime" in start else None,
                "due_date": date.fromisoformat(start["date"]) if "date" in start else None,
                "timezone": start.get("timeZone", "UTC"),
                "recurrence": next((line[6:] for line in recurrence if line.startswith("RRULE:")), None),
            }
        else:
            from app.integrations.calendar.caldav import validate_ical

            resource = validate_ical(remote["payload"]["icalendar"])
            event = next(e for e in resource.walk("VEVENT") if not e.get("RECURRENCE-ID"))
            start = event.decoded("DTSTART")
            values = {
                "due_at": start if isinstance(start, datetime) else None,
                "due_date": None if isinstance(start, datetime) else start,
                "timezone": str(event["DTSTART"].params.get("TZID", "UTC")),
                "recurrence": event["RRULE"].to_ical().decode() if "RRULE" in event else None,
            }
        # TaskOut's planner recurrence parser is deliberately not called for an
        # external rule: the calendar owns the complete RFC recurrence grammar.
        return projection.model_copy(update=values)
    except (CalendarError, OAuthFlowError, ValueError, KeyError, StopIteration):
        raise PlannerConflict("planner_source_unavailable") from None
