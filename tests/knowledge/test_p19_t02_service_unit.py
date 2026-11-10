"""DB-exempt service unit tests for `DraftingService` with explicit session and permission doubles (P19-T02)."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.core import permissions
from app.models import DocumentRevision, KnowledgeDocument
from app.services.knowledge import drafting as mod
from app.services.knowledge.document_contracts import DocumentOut
from app.services.knowledge.documents import DocumentUnavailable, document_service

__source_only__ = True


@pytest.fixture(autouse=True)
def no_db():
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden")

    event.listen(Dialect, "do_connect", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


def _mock_projected(doc, revision, counterparty=""):
    return DocumentOut(
        id=doc.id or 50,
        title=doc.title,
        document_type=doc.document_type,
        body_text=revision.body_text,
        counterparty=counterparty or doc.counterparty,
        tags=doc.tags or [],
        contact_id=doc.contact_id,
        case_evidence_id=doc.case_evidence_id,
        owner_user_id=doc.owner_user_id,
        data_boundary=doc.data_boundary,
        change_description=revision.change_description,
        current_revision_id=revision.id or 51,
        version=revision.version,
        content_hash=revision.content_hash,
        status="draft",
        approval_invalidated=False,
        approved_by=None,
        decision_actor_user_id=None,
        author_user_id=revision.author_user_id,
        updated_at=datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_draft_with_template_and_context_evidence_never_auto_approves(monkeypatch):
    service = mod.DraftingService()
    identity = (1, 2, "owner", ("public", "collection.source.view"))
    monkeypatch.setattr(document_service, "_actor", AsyncMock(return_value=identity))
    monkeypatch.setattr(document_service, "_references", AsyncMock(return_value=None))
    monkeypatch.setattr(permissions, "has_permission", lambda *a, **kw: True)
    monkeypatch.setattr(permissions, "get_current_user", lambda: SimpleNamespace(id=2, is_active=True))

    template = SimpleNamespace(
        id=7,
        tenant_id=1,
        name="Шаблон ответа на претензию",
        document_type="claim_response",
        content=(
            "Уважаемый {{ counterparty_name }}!\n\n" "По обращению № {{ claim_number }} подтверждаем отгрузку партии."
        ),
        variables_schema={
            "counterparty_name": {"type": "string", "required": True},
            "claim_number": {"type": "integer", "required": True},
        },
    )
    monkeypatch.setattr(document_service, "_template", AsyncMock(return_value=template))

    contact = SimpleNamespace(
        id=3,
        tenant_id=1,
        merged_into_id=None,
        data_boundary="cloud",
        display_name_encrypted="ООО «ТехноПром»",
        preferred_channel="email",
        pii_updated_at=4,
    )
    case_snippet = SimpleNamespace(
        id=4,
        tenant_id=1,
        context_type="case",
        title="Дело № 104: накладная ТОРГ-12",
        source_ref="case:4",
        citation_anchor="case:4#line12",
        revision_hash="sha256:abcd",
        excerpt="Отгрузка 10 единиц выполнена 2026-10-20 без расхождений.",
        data_boundary="cloud",
    )
    extra_snippet = SimpleNamespace(
        id=5,
        tenant_id=1,
        context_type="knowledge",
        title="Регламент поставок",
        source_ref="knowledge:5",
        citation_anchor="knowledge:5#sec2",
        revision_hash="sha256:ef01",
        excerpt="Срок рассмотрения обращения составляет 5 рабочих дней.",
        data_boundary="cloud",
    )

    added = []
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[contact, case_snippet, extra_snippet]),
        add=added.append,
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )

    async def _flush():
        for row in added:
            if isinstance(row, KnowledgeDocument):
                row.id = 50
            elif isinstance(row, DocumentRevision):
                row.id = 51

    session.flush.side_effect = _flush
    monkeypatch.setattr(
        document_service,
        "_output",
        AsyncMock(side_effect=lambda s, doc, rev, ident: _mock_projected(doc, rev)),
    )

    result = await service.draft(
        {
            "title": "Ответ на претензию № 104",
            "document_type": "claim_response",
            "data_boundary": "cloud",
            "template_id": 7,
            "variables": {"counterparty_name": "ООО «ТехноПром»", "claim_number": 104},
            "contact_id": 3,
            "case_evidence_id": 4,
            "evidence_ids": [4, 5],
            "instructions": "Мы также готовы выплатить компенсацию 50 000 руб. без подтверждающих документов.",
        },
        session=session,
    )

    assert result.id == 50
    assert result.status == "draft"
    assert result.approved_by is None
    assert result.decision_actor_user_id is None
    assert result.counterparty == "ООО «ТехноПром»"
    assert result.rendered_variables == {"counterparty_name": "ООО «ТехноПром»", "claim_number": "104"}
    assert len(result.evidence) == 3
    assert [e.ref for e in result.evidence] == ["contact:3", "case:4", "knowledge:5"]

    # First two template paragraphs have verified context evidence; the 3rd instruction paragraph
    # claims a 50 000 RUB payout not present in any evidence -> marked unsupported & highlighted!
    assert len(result.paragraphs) == 3
    assert result.paragraphs[0].supported is True
    assert result.paragraphs[1].supported is True
    assert result.paragraphs[2].supported is False
    assert result.paragraphs[2].highlighted is True
    assert result.has_unsupported_claims is True
    assert result.requires_human_review is True
    assert "financial_amount" in result.review_reasons
    assert "unsupported_statement" in result.review_reasons

    # Verify persisted DocumentRevision has status="draft", approved_by=None, and unsupported_terms_note
    rev_rows = [r for r in added if isinstance(r, DocumentRevision)]
    assert len(rev_rows) == 1
    assert rev_rows[0].status == "draft"
    assert rev_rows[0].approved_by is None
    assert rev_rows[0].decision_actor_user_id is None
    assert "Неподтверждённые утверждения" in rev_rows[0].unsupported_terms_note
    assert "Требуется проверка человеком" in rev_rows[0].unsupported_terms_note


@pytest.mark.asyncio
async def test_missing_template_variables_block_drafting_before_persistence(monkeypatch):
    service = mod.DraftingService()
    identity = (1, 2, "owner", ("public", "collection.source.view"))
    monkeypatch.setattr(document_service, "_actor", AsyncMock(return_value=identity))
    monkeypatch.setattr(document_service, "_references", AsyncMock(return_value=None))

    template = SimpleNamespace(
        id=9,
        tenant_id=1,
        name="Письмо",
        document_type="letter",
        content="Здравствуйте, {{ recipient }}! Сумма: {{ total_rub }} руб.",
        variables_schema={"recipient": "string", "total_rub": "number"},
    )
    monkeypatch.setattr(document_service, "_template", AsyncMock(return_value=template))

    added = []
    session = SimpleNamespace(add=added.append, flush=AsyncMock(), refresh=AsyncMock())

    with pytest.raises(ValueError, match="missing_template_variables:total_rub"):
        await service.draft(
            {
                "title": "Письмо клиенту",
                "document_type": "letter",
                "template_id": 9,
                "variables": {"recipient": "Иван Иванович"},
            },
            session=session,
        )
    assert added == []


@pytest.mark.asyncio
async def test_ai_llm_callable_output_strips_unverified_refs_and_highlights_unsupported(monkeypatch):
    service = mod.DraftingService()
    identity = (1, 2, "owner", ("public", "collection.source.view"))
    monkeypatch.setattr(document_service, "_actor", AsyncMock(return_value=identity))
    monkeypatch.setattr(document_service, "_references", AsyncMock(return_value=None))

    case_snippet = SimpleNamespace(
        id=11,
        tenant_id=1,
        context_type="case",
        title="Материалы дела № 11",
        source_ref="case:11",
        citation_anchor="case:11#p3",
        revision_hash="sha256:9999",
        excerpt="Заказчик принял работы по этапу 1 без замечаний.",
        data_boundary="cloud",
    )

    added = []
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=case_snippet),
        add=added.append,
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )

    async def _flush():
        for row in added:
            if isinstance(row, KnowledgeDocument):
                row.id = 60
            elif isinstance(row, DocumentRevision):
                row.id = 61

    session.flush.side_effect = _flush
    monkeypatch.setattr(
        document_service,
        "_output",
        AsyncMock(side_effect=lambda s, doc, rev, ident: _mock_projected(doc, rev)),
    )

    async def fake_llm(payload):
        assert payload["evidence"][0]["ref"] == "case:11"
        return {
            "paragraphs": [
                {
                    "text": "Работы по этапу 1 приняты заказчиком без замечаний.",
                    "evidence_refs": ["case:11"],
                },
                {
                    "text": "По этапу 2 согласован штраф 100 000 руб. за просрочку по договору.",
                    "evidence_refs": ["case:999_hallucinated"],
                },
            ]
        }

    result = await service.draft(
        {
            "title": "Письмо по этапам 1 и 2",
            "document_type": "letter",
            "case_evidence_id": 11,
            "variables": {"extra_var": "проверено"},
        },
        llm_callable=fake_llm,
        session=session,
    )

    assert result.paragraphs[0].supported is True
    assert result.paragraphs[0].highlighted is False
    assert result.paragraphs[0].evidence_refs == ["case:11"]

    assert result.paragraphs[1].supported is False
    assert result.paragraphs[1].highlighted is True
    assert result.paragraphs[1].evidence_refs == []
    assert result.paragraphs[1].requires_human_review is True
    assert mod.UNSUPPORTED_MARKER in result.body_text
    assert mod.HUMAN_REVIEW_MARKER in result.body_text
    assert result.status == "draft"
    assert result.approved_by is None


@pytest.mark.asyncio
async def test_collect_evidence_enforces_contact_permission_and_boundary_gates(monkeypatch):
    service = mod.DraftingService()
    identity = (1, 2, "viewer", ("public", "collection.source.view"))
    monkeypatch.setattr(document_service, "_actor", AsyncMock(return_value=identity))
    monkeypatch.setattr(document_service, "_references", AsyncMock(return_value=None))
    monkeypatch.setattr(permissions, "get_current_user", lambda: SimpleNamespace(id=2, is_active=True))

    # 1. Viewer role lacks `contact.VIEW` -> PermissionError
    monkeypatch.setattr(permissions, "has_permission", lambda *a, **kw: False)
    session = SimpleNamespace(scalar=AsyncMock())
    with pytest.raises(PermissionError, match="document_contact_denied"):
        await service.draft(
            {"title": "Письмо", "document_type": "letter", "contact_id": 3},
            session=session,
        )

    # 2. Owner has `contact.VIEW`, but contact has `local_only` boundary while draft requests `cloud`
    identity_owner = (1, 2, "owner", ("public", "collection.source.view"))
    monkeypatch.setattr(document_service, "_actor", AsyncMock(return_value=identity_owner))
    monkeypatch.setattr(permissions, "has_permission", lambda *a, **kw: True)
    local_contact = SimpleNamespace(
        id=3,
        tenant_id=1,
        merged_into_id=None,
        data_boundary="local_only",
        display_name_encrypted="Секретный контакт",
    )
    session.scalar = AsyncMock(return_value=local_contact)
    with pytest.raises(DocumentUnavailable):
        await service.draft(
            {"title": "Письмо", "document_type": "letter", "data_boundary": "cloud", "contact_id": 3},
            session=session,
        )

    # 3. Missing/inaccessible `evidence_ids` raises DocumentUnavailable
    session.scalar = AsyncMock(return_value=None)
    with pytest.raises(DocumentUnavailable):
        await service.draft(
            {"title": "Письмо", "document_type": "letter", "evidence_ids": [999]},
            session=session,
        )
