"""P14-T03 unit and contract tests for CRM contact cards, deal summaries, and duplicate candidates.

Covers:
- `build_portal_entity_url` deep-links (`https://<portal>/crm/<kind>/details/<id>/`).
- `CrmContactCardService.list_crm_contacts` with filters (`entity_kind`, `updated_since`, `has_birthday`, `connection_id`).
- `CrmContactCardService.get_crm_contact_card`:
  - returns only the current workspace's (`tenant_id`) data; cross-workspace entity IDs return 404 (`crm_entity_not_found`).
  - aggregates linked deals (`total_count`, `open_count`, `closed_count`, `total_opportunity_amount`).
  - surfaces deterministic, explainable duplicate candidates (`matched_kinds`: `["email"]`, `["phone"]`, `["email", "phone"]`)
    without performing any automatic merge (R-16).
  - marks birthday and anniversary as read-only (`birthday_read_only=True`, `anniversary_read_only=True`).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet

os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("POSTGRES_URL", "postgresql+asyncpg://user:pass@localhost:5432/test_db")
os.environ.setdefault("CREDENTIALS_KEY", Fernet.generate_key().decode("ascii"))
os.environ.setdefault("PII_ENCRYPTION_KEYS", Fernet.generate_key().decode("ascii"))
os.environ.setdefault("PII_INDEX_KEY", "unit-test-pii-index-key-0123456789abcdef0123456789abcdef")

import pytest

__source_only__ = True

from app.core.config import settings
from app.core.tenant_context import tenant_scope
from app.integrations.bitrix24.client import Bitrix24Error
from app.models.contacts.core import Contact, ContactIdentity
from app.models.crm.entity import CrmEntity
from app.models.integrations.connection import WorkspaceConnection
from app.services.contacts.identity import identity_index
from app.services.crm.cards import CrmContactCardService, build_portal_entity_url


@pytest.fixture(autouse=True)
def _pii_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(settings, "PII_INDEX_KEY", "unit-test-pii-index-key-0123456789abcdef0123456789abcdef")


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_ScalarResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class FakeCardsSession:
    def __init__(self) -> None:
        self.connections: list[WorkspaceConnection] = []
        self.contacts: list[Contact] = []
        self.identities: list[ContactIdentity] = []
        self.crm_entities: list[CrmEntity] = []

    async def execute(self, stmt: Any) -> _ScalarResult:
        params = stmt.compile().params
        tables = {getattr(f, "name", "") for f in stmt.get_final_froms()}
        tid = params.get("tenant_id_1")
        if "workspace_connections" in tables:
            return _ScalarResult([c for c in self.connections if c.tenant_id == tid])
        if "contacts" in tables and "contact_identities" not in tables:
            return _ScalarResult([c for c in self.contacts if c.tenant_id == tid])
        if "contact_identities" in tables:
            return _ScalarResult([i for i in self.identities if i.tenant_id == tid])
        if "crm_entities" in tables:
            kind = params.get("entity_kind_1")
            conn_id = params.get("connection_id_1")
            rows = [
                e
                for e in self.crm_entities
                if e.tenant_id == tid
                and not e.is_deleted_remote
                and (kind is None or e.entity_kind == kind)
                and (conn_id is None or e.connection_id == conn_id)
            ]
            return _ScalarResult(rows)
        return _ScalarResult([])


def test_p14_t03_build_portal_entity_url() -> None:
    assert (
        build_portal_entity_url("acme.bitrix24.ru", "contact", "42")
        == "https://acme.bitrix24.ru/crm/contact/details/42/"
    )
    assert (
        build_portal_entity_url("acme.bitrix24.ru", "deal", "99")
        == "https://acme.bitrix24.ru/crm/deal/details/99/"
    )
    assert build_portal_entity_url("", "contact", "42") is None
    assert build_portal_entity_url("acme.bitrix24.ru", "contact", "") is None


@pytest.mark.asyncio
async def test_p14_t03_cards_list_detail_deals_and_duplicates() -> None:
    session = FakeCardsSession()
    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    session.connections.append(
        WorkspaceConnection(
            id=1,
            tenant_id=10,
            owner_user_id=101,
            platform="bitrix24",
            provider_id="bitrix24",
            auth_method="oauth2",
            label="Acme",
            account_key="acme.bitrix24.ru",
            config={"portal_domain": "acme.bitrix24.ru"},
            is_active=True,
            state="verified",
        )
    )
    c1 = Contact(
        id=1,
        tenant_id=10,
        display_name_encrypted="Смирнова Анна",
        birthday_encrypted="1992-03-15",
        birthday_has_year=True,
        preferred_channel="email",
    )
    c2 = Contact(
        id=2,
        tenant_id=10,
        display_name_encrypted="Смирнова Анна (вторая карточка)",
        birthday_encrypted=None,
        birthday_has_year=False,
        preferred_channel="phone",
    )
    c_other_tenant = Contact(
        id=3,
        tenant_id=20,
        display_name_encrypted="Чужой Контакт",
        birthday_encrypted="1990-01-01",
        birthday_has_year=True,
    )
    session.contacts.extend([c1, c2, c_other_tenant])

    shared_email_bidx = identity_index("email", "anna@example.com")
    shared_phone_bidx = identity_index("phone", "+79991112233")

    session.identities.extend(
        [
            ContactIdentity(
                id=1,
                tenant_id=10,
                contact_id=1,
                kind="email",
                connection_id=1,
                value_encrypted="anna@example.com",
                blind_index=shared_email_bidx,
                verified=True,
            ),
            ContactIdentity(
                id=2,
                tenant_id=10,
                contact_id=1,
                kind="phone",
                connection_id=1,
                value_encrypted="+79991112233",
                blind_index=shared_phone_bidx,
                verified=False,
            ),
        ]
    )

    ent1 = CrmEntity(
        id=11,
        tenant_id=10,
        connection_id=1,
        provider="bitrix24",
        entity_kind="contact",
        external_id="101",
        contact_id=1,
        company_external_id="301",
        source_modified_at=now,
        last_synced_at=now,
        title_encrypted="Смирнова Анна",
        personal_fields_encrypted=json.dumps(
            {
                "birthday": "1992-03-15",
                "birthday_has_year": True,
                "email_blind_indexes": [shared_email_bidx],
                "phone_blind_indexes": [shared_phone_bidx],
            }
        ),
    )
    ent2_dup = CrmEntity(
        id=12,
        tenant_id=10,
        connection_id=1,
        provider="bitrix24",
        entity_kind="lead",
        external_id="401",
        contact_id=2,
        source_modified_at=now,
        last_synced_at=now,
        title_encrypted="Лид: Анна Смирнова",
        personal_fields_encrypted=json.dumps(
            {
                "email_blind_indexes": [shared_email_bidx],
                "phone_blind_indexes": [shared_phone_bidx],
            }
        ),
    )
    deal_open = CrmEntity(
        id=13,
        tenant_id=10,
        connection_id=1,
        provider="bitrix24",
        entity_kind="deal",
        external_id="501",
        contact_id=1,
        stage_code="C1:PREPARATION",
        currency_code="RUB",
        opportunity_amount=150000.0,
        is_closed=False,
        source_modified_at=now,
        last_synced_at=now,
        title_encrypted="Сделка 1 (в работе)",
    )
    deal_closed = CrmEntity(
        id=14,
        tenant_id=10,
        connection_id=1,
        provider="bitrix24",
        entity_kind="deal",
        external_id="502",
        contact_id=1,
        stage_code="C1:WON",
        currency_code="RUB",
        opportunity_amount=350000.0,
        is_closed=True,
        source_modified_at=now,
        last_synced_at=now,
        title_encrypted="Сделка 2 (успешна)",
    )
    ent_other_tenant = CrmEntity(
        id=99,
        tenant_id=20,
        connection_id=2,
        provider="bitrix24",
        entity_kind="contact",
        external_id="999",
        contact_id=3,
        source_modified_at=now,
        last_synced_at=now,
        title_encrypted="Чужой Контакт",
        personal_fields_encrypted=json.dumps({"email_blind_indexes": [shared_email_bidx]}),
    )
    session.crm_entities.extend([ent1, ent2_dup, deal_open, deal_closed, ent_other_tenant])

    svc = CrmContactCardService()
    with tenant_scope(10):
        bday_list = await svc.list_crm_contacts(
            tenant_id=10,
            actor_id=101,
            has_birthday=True,
            session=session,
        )
        assert bday_list["total"] == 3  # ent1 + 2 deals linked to c1 (which has birthday)
        only_contacts = await svc.list_crm_contacts(
            tenant_id=10,
            actor_id=101,
            entity_kind="contact",
            has_birthday=True,
            session=session,
        )
        assert only_contacts["total"] == 1
        assert only_contacts["items"][0]["id"] == 11
        assert only_contacts["items"][0]["read_only_dates"] is True
        assert only_contacts["items"][0]["portal_url"] == "https://acme.bitrix24.ru/crm/contact/details/101/"

        card = await svc.get_crm_contact_card(
            tenant_id=10,
            actor_id=101,
            crm_entity_id=11,
            session=session,
        )
        assert card["title"] == "Смирнова Анна"
        assert card["birthday"] == "1992-03-15"
        assert card["birthday_read_only"] is True
        assert card["anniversary_read_only"] is True
        assert card["deals_summary"]["total_count"] == 2
        assert card["deals_summary"]["open_count"] == 1
        assert card["deals_summary"]["closed_count"] == 1
        assert card["deals_summary"]["total_opportunity_amount"] == 500000.0
        assert len(card["duplicate_candidates"]) == 1
        dup = card["duplicate_candidates"][0]
        assert dup["candidate_crm_entity_id"] == 12
        assert dup["matched_kinds"] == ["email", "phone"]
        assert "email, phone" in dup["explanation"]

        # Cross-tenant entity ID 99 is completely invisible to tenant 10
        with pytest.raises(Bitrix24Error) as exc_info:
            await svc.get_crm_contact_card(tenant_id=10, actor_id=101, crm_entity_id=99, session=session)
        assert exc_info.value.code == "crm_entity_not_found"
