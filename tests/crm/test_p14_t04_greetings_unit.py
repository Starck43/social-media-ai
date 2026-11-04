"""P14-T04 unit and contract tests for upcoming birthday/anniversary events and AI greeting drafts.

Covers:
- `next_occurrence_date`: leap year handling (`Feb 29 -> Feb 28` in non-leap years),
  `--MM-DD` dates without year, `YYYY-MM-DD` with `years_turning`, and reminder day calculation (`7`, `3`, `1` days).
- `CrmGreetingsService.list_upcoming_events`: filters by horizon and `reminder_days_only`,
  creates 7/3/1-day reminder notifications when `emit_notifications=True`, and isolates workspaces.
- `CrmGreetingsService.generate_greeting_draft`:
  - restricts LLM prompt context strictly to allowlisted fields (`ALLOWLISTED_CONTEXT_KEYS`);
  - enforces payer/budget check (`llm_budget_exceeded` -> 402);
  - stores encrypted draft in `GreetingHistory` (`subject_encrypted`, `draft_text_encrypted`);
  - prevents repeat greetings when `GreetingHistory` for `(tenant_id, contact_id, event_type, year)`
    is already `approved` or `sent` (`greeting_already_processed` -> 409);
  - keeps `send_enabled=False` until P14-T05.
- PII rotation `REGISTRY` includes `greeting_history` encrypted columns.
"""

from __future__ import annotations

import json
import os
from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet

os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("POSTGRES_URL", "postgresql+asyncpg://user:pass@localhost:5432/test_db")
os.environ.setdefault("CREDENTIALS_KEY", Fernet.generate_key().decode("ascii"))
os.environ.setdefault("PII_ENCRYPTION_KEYS", Fernet.generate_key().decode("ascii"))
os.environ.setdefault("PII_INDEX_KEY", "unit-test-pii-index-key-0123456789abcdef0123456789abcdef")

import pytest

__source_only__ = True

from app.core.config import settings
from app.core.tenant_context import TenantContextError, tenant_scope
from app.integrations.bitrix24.client import Bitrix24Error
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.contacts.core import Contact
from app.models.crm.entity import CrmEntity
from app.models.crm.greeting_history import GreetingHistory
from app.models.managers.crm_entity_manager import GreetingHistoryManager
from app.models.notifications.notification import Notification
from app.services.crm.greetings import (
    ALLOWLISTED_CONTEXT_KEYS,
    CrmGreetingsService,
    GreetingDraftOutput,
    next_occurrence_date,
)
from cli.commands.pii import REGISTRY


@pytest.fixture(autouse=True)
def _pii_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(settings, "PII_INDEX_KEY", "unit-test-pii-index-key-0123456789abcdef0123456789abcdef")


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> "_ScalarResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class FakeGreetingsSession:
    def __init__(self) -> None:
        self.contacts: dict[int, Contact] = {}
        self.crm_entities: list[CrmEntity] = []
        self.history: dict[int, GreetingHistory] = {}
        self.notifications: list[Notification] = []
        self._next_id = 1

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = self._next_id
            self._next_id += 1
        if isinstance(obj, Contact):
            self.contacts[obj.id] = obj
        elif isinstance(obj, CrmEntity):
            self.crm_entities.append(obj)
        elif isinstance(obj, GreetingHistory):
            self.history[obj.id] = obj
        elif isinstance(obj, Notification):
            self.notifications.append(obj)

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any) -> _ScalarResult:
        params = stmt.compile().params
        tables = {getattr(f, "name", "") for f in stmt.get_final_froms()}
        tid = params.get("tenant_id_1")

        if "contacts" in tables:
            cid = params.get("id_1")
            rows = [
                c
                for c in self.contacts.values()
                if c.tenant_id == tid and (cid is None or c.id == cid)
            ]
            return _ScalarResult(rows)

        if "crm_entities" in tables:
            return _ScalarResult([e for e in self.crm_entities if e.tenant_id == tid and not e.is_deleted_remote])

        if "greeting_history" in tables:
            cid = params.get("contact_id_1")
            ev_type = params.get("event_type_1")
            yr = params.get("year_1")
            rows = [
                h
                for h in self.history.values()
                if h.tenant_id == tid
                and (cid is None or h.contact_id == cid)
                and (ev_type is None or h.event_type == ev_type)
                and (yr is None or h.year == yr)
            ]
            return _ScalarResult(rows)

        return _ScalarResult([])


def test_p14_t04_next_occurrence_date_and_leap_year() -> None:
    # 2026 is a non-leap year: Feb 29 maps to Feb 28!
    ev_date, days_until, years = next_occurrence_date("1996-02-29", today=date(2026, 2, 21))
    assert ev_date == date(2026, 2, 28)
    assert days_until == 7
    assert years == 30

    # Date without year (--MM-DD)
    ev2, days2, years2 = next_occurrence_date("--11-07", today=date(2026, 11, 4))
    assert ev2 == date(2026, 11, 7)
    assert days2 == 3
    assert years2 is None

    # Past date in current year wraps to next year
    ev3, days3, years3 = next_occurrence_date("1990-01-02", today=date(2026, 12, 30))
    assert ev3 == date(2027, 1, 2)
    assert days3 == 3
    assert years3 == 37


@pytest.mark.asyncio
async def test_p14_t04_upcoming_events_7_3_1_days_and_ai_drafts(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeGreetingsSession()
    ref_today = date(2026, 11, 4)

    # Contact 1: birthday in 7 days (2026-11-11), anniversary in 1 day (2026-11-05)
    c1 = Contact(
        id=1,
        tenant_id=10,
        display_name_encrypted="Елена Волкова",
        birthday_encrypted="1991-11-11",
        birthday_has_year=True,
        anniversary_encrypted="2020-11-05",
        anniversary_has_year=True,
        preferred_channel="email",
    )
    # Contact 2: birthday in 3 days (2026-11-07)
    c2 = Contact(
        id=2,
        tenant_id=10,
        display_name_encrypted="Дмитрий Орлов",
        birthday_encrypted="--11-07",
        birthday_has_year=False,
        preferred_channel="telegram",
    )
    # Contact 3: birthday in 5 days (not a 7/3/1 reminder bucket)
    c3 = Contact(
        id=3,
        tenant_id=10,
        display_name_encrypted="Ольга Морозова",
        birthday_encrypted="--11-09",
        birthday_has_year=False,
    )
    session.add(c1)
    session.add(c2)
    session.add(c3)
    session.add(
        CrmEntity(
            id=21,
            tenant_id=10,
            connection_id=1,
            provider="bitrix24",
            entity_kind="company",
            external_id="300",
            last_synced_at=ref_today,  # type: ignore[arg-type]
            title_encrypted="АО Вектор",
        )
    )
    session.add(
        CrmEntity(
            id=22,
            tenant_id=10,
            connection_id=1,
            provider="bitrix24",
            entity_kind="contact",
            external_id="100",
            contact_id=1,
            company_external_id="300",
            last_synced_at=ref_today,  # type: ignore[arg-type]
            title_encrypted="Елена Волкова",
            personal_fields_encrypted=json.dumps(
                {
                    "post": "Финансовый директор",
                    "emails": ["secret_email_not_for_prompt@example.com"],
                }
            ),
        )
    )

    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", AsyncMock(return_value=SimpleNamespace(id=1)))
    svc = CrmGreetingsService()

    with tenant_scope(10):
        upcoming = await svc.list_upcoming_events(
            tenant_id=10,
            actor_id=101,
            horizon_days=10,
            reminder_days_only=True,
            reference_date=ref_today,
            emit_notifications=True,
            session=session,
        )
        assert [item["days_until"] for item in upcoming["items"]] == [1, 3, 7]
        assert upcoming["notifications_created"] == 3
        assert len(session.notifications) == 3

        # Verify mocked LLM generator receives strictly allowlisted context (no raw email/phone)
        captured_ctx: dict[str, Any] = {}

        def fake_llm(ctx: dict[str, Any]) -> GreetingDraftOutput:
            captured_ctx.update(ctx)
            return GreetingDraftOutput(
                subject=f"Персональное поздравление для {ctx['display_name']}",
                draft_text=f"Уважаемая {ctx['display_name']} ({ctx.get('company_title')}), поздравляем!",
                tone=ctx["tone"],
                ai_generated=True,
            )

        draft = await svc.generate_greeting_draft(
            tenant_id=10,
            actor_id=101,
            contact_id=1,
            event_type="birthday",
            year=2026,
            reference_date=ref_today,
            llm_generator=fake_llm,
            session=session,
        )
        assert set(captured_ctx.keys()) <= ALLOWLISTED_CONTEXT_KEYS
        assert "emails" not in captured_ctx
        assert captured_ctx["company_title"] == "АО Вектор"
        assert captured_ctx["post"] == "Финансовый директор"
        assert captured_ctx["years_turning"] == 35
        assert draft["status"] == "draft"
        assert draft["ai_generated"] is True
        assert draft["send_enabled"] is False

        # Once status is marked 'sent' or 'approved', repeat draft generation for same contact/event/year is refused (409)
        hist_row = session.history[draft["id"]]
        hist_row.status = "sent"
        with pytest.raises(Bitrix24Error) as exc_repeat:
            await svc.generate_greeting_draft(
                tenant_id=10,
                actor_id=101,
                contact_id=1,
                event_type="birthday",
                year=2026,
                reference_date=ref_today,
                session=session,
            )
        assert exc_repeat.value.code == "greeting_already_processed"
        assert exc_repeat.value.status_code == 409

        # Budget denial returns 402 llm_budget_exceeded
        with pytest.raises(Bitrix24Error) as exc_budget:
            await svc.generate_greeting_draft(
                tenant_id=10,
                actor_id=101,
                contact_id=2,
                event_type="birthday",
                year=2026,
                reference_date=ref_today,
                budget_checker=lambda **_: False,
                session=session,
            )
        assert exc_budget.value.code == "llm_budget_exceeded"
        assert exc_budget.value.status_code == 402

    with pytest.raises(TenantContextError):
        GreetingHistoryManager().get_queryset(session=session)


def test_p14_t04_pii_registry_includes_greeting_history() -> None:
    registered = {(t, c) for t, c, _ in REGISTRY}
    assert ("greeting_history", "subject_encrypted") in registered
    assert ("greeting_history", "draft_text_encrypted") in registered
