"""LOCAL PostgreSQL behavioural checks for templates and evidence-grounded AI drafting (P19-T02).

Requires the validated ``test_schema`` at the current head revision with exclusive writer
ownership. Synthetic rows belong to ``personal_world`` only and are removed, then verified
absent, in the ``draft_world`` teardown.
"""

import json
from contextlib import ExitStack

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select, text, update

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import Contact, DocumentRevision, KnowledgeDocument, KnowledgeSnippet, Tenant, User
from app.models.knowledge import DocumentTemplate
from app.services.knowledge.document_contracts import TemplateCreate
from app.services.knowledge.documents import DocumentConflict, DocumentUnavailable, document_service
from app.services.knowledge.drafting import (
    HUMAN_REVIEW_MARKER,
    UNSUPPORTED_MARKER,
    drafting_service,
)
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


def scope(world, workspace=0, role="owner"):
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[workspace].id))
    stack.enter_context(permission_scope(world.users[0 if workspace == 0 else 2], member_grants=workspace_grants(role)))
    return stack


@pytest.fixture
async def draft_world(personal_world, monkeypatch):  # noqa: F811
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    world = personal_world
    ids = [tenant.id for tenant in world.tenants]
    try:
        yield world
    finally:
        async with async_session_maker() as session, session.begin():
            for model in (DocumentTemplate, KnowledgeDocument, KnowledgeSnippet, Contact):
                await session.execute(delete(model).where(model.tenant_id.in_(ids)))
        async with async_session_maker() as session:
            for model in (DocumentTemplate, DocumentRevision, KnowledgeDocument, KnowledgeSnippet, Contact):
                count = await session.scalar(select(func.count()).select_from(model).where(model.tenant_id.in_(ids)))
                assert count == 0


async def _seed_contact_and_case(world, workspace=0):
    async with async_session_maker() as session, session.begin():
        contact = Contact(
            tenant_id=world.tenants[workspace].id,
            display_name_encrypted="ООО «СтройИнвест»",
            preferred_channel="email",
        )
        session.add(contact)
        case_snip = KnowledgeSnippet(
            tenant_id=world.tenants[workspace].id,
            context_type="case",
            title="Акт приёмки КС-2 № 18",
            source_ref="case:18",
            citation_anchor="КС-2 № 18, п. 3",
            data_boundary="cloud",
            acl_scope="collection.source.view",
            revision_hash="sha256:" + "1" * 64,
            excerpt="Работы по монтажу вентиляции выполнены в полном объёме 2026-10-25.",
            extraction_limit_note="",
        )
        session.add(case_snip)
        await session.flush()
        return contact.id, case_snip.id


async def test_template_rendering_missing_variable_blocks_and_ai_draft_persists_honestly(draft_world):
    world = draft_world
    contact_id, case_id = await _seed_contact_and_case(world, workspace=0)

    with scope(world, workspace=0, role="owner"):
        tpl = await document_service.create_template(
            TemplateCreate(
                name="Ответ на претензию по КС-2",
                document_type="claim_response",
                content=(
                    "Уважаемый руководитель {{ company_name }}!\n\n"
                    "В ответ на претензию № {{ claim_no }} от {{ claim_date }} сообщаем, "
                    "что монтаж вентиляции завершён."
                ),
                variables_schema={
                    "company_name": {"type": "string", "required": True},
                    "claim_no": {"type": "integer", "required": True},
                    "claim_date": {"type": "date", "required": True},
                },
            )
        )

        # 1. Missing `claim_date` blocks rendering and creates zero documents
        async with async_session_maker() as session:
            documents_before = await session.scalar(
                select(func.count())
                .select_from(KnowledgeDocument)
                .where(KnowledgeDocument.tenant_id == world.tenants[0].id)
            )
        with pytest.raises(ValueError, match="missing_template_variables:claim_date"):
            await drafting_service.draft(
                {
                    "title": "Неполный черновик",
                    "document_type": "claim_response",
                    "template_id": tpl.id,
                    "variables": {"company_name": "ООО «СтройИнвест»", "claim_no": 18},
                }
            )
        async with async_session_maker() as session:
            documents_after = await session.scalar(
                select(func.count())
                .select_from(KnowledgeDocument)
                .where(KnowledgeDocument.tenant_id == world.tenants[0].id)
            )
        assert documents_after == documents_before

        # 2. Valid template + allowlisted CRM context + Case evidence + AI LLM output with one supported
        #    and one unsupported legal/financial claim
        def fake_llm(payload):
            case_ref = f"case:{case_id}"
            return {
                "paragraphs": [
                    {
                        "text": "Работы по монтажу вентиляции выполнены в полном объёме 2026-10-25.",
                        "evidence_refs": [case_ref],
                    },
                    {
                        "text": "Дополнительно обязуемся выплатить неустойку 250 000 руб. по мировому соглашению.",
                        "evidence_refs": ["case:999999"],
                    },
                ]
            }

        drafted = await drafting_service.draft(
            {
                "title": "Официальный ответ по КС-2 № 18",
                "document_type": "claim_response",
                "data_boundary": "cloud",
                "template_id": tpl.id,
                "variables": {
                    "company_name": "ООО «СтройИнвест»",
                    "claim_no": 18,
                    "claim_date": "2026-11-01",
                },
                "contact_id": contact_id,
                "case_evidence_id": case_id,
            },
            llm_callable=fake_llm,
        )

        assert drafted.status == "draft"
        assert drafted.approved_by is None
        assert drafted.decision_actor_user_id is None
        assert drafted.counterparty == "ООО «СтройИнвест»"
        assert [item.ref for item in drafted.evidence] == [f"case:{case_id}"]
        assert drafted.has_unsupported_claims is True
        assert drafted.requires_human_review is True
        assert UNSUPPORTED_MARKER in drafted.body_text
        assert HUMAN_REVIEW_MARKER in drafted.body_text

        # Verify persisted DocumentRevision in PostgreSQL
        async with async_session_maker() as session:
            rev = await session.scalar(
                select(DocumentRevision).where(DocumentRevision.id == drafted.current_revision_id)
            )
            assert rev is not None
            assert rev.status == "draft"
            assert rev.approved_by is None
            assert "Неподтверждённые утверждения (1)" in rev.unsupported_terms_note
            assert "Требуется проверка человеком" in rev.unsupported_terms_note

    # 3. Cross-tenant isolation: tenant 1 cannot draft from tenant 0's template or case evidence
    with scope(world, workspace=1, role="owner"):
        with pytest.raises(DocumentUnavailable):
            await drafting_service.draft(
                {
                    "title": "Чужой шаблон",
                    "document_type": "letter",
                    "template_id": tpl.id,
                    "variables": {
                        "company_name": "ООО «СтройИнвест»",
                        "claim_no": 18,
                        "claim_date": "2026-11-01",
                    },
                }
            )
        with pytest.raises(DocumentUnavailable):
            await drafting_service.draft(
                {
                    "title": "Чужое дело",
                    "document_type": "letter",
                    "case_evidence_id": case_id,
                }
            )


async def test_provider_runs_without_identity_locks_and_context_changes_abort_persistence(draft_world, monkeypatch):
    from app.services.ai import llm_client

    world = draft_world
    _, case_id = await _seed_contact_and_case(world)
    tenant_id = world.tenants[0].id
    user_id = world.users[0].id

    provider_calls = 0

    async def lock_probe(messages, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        payload = json.loads(messages[1]["content"])
        async with async_session_maker() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '750ms'"))
            await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
            await session.scalar(select(User.id).where(User.id == user_id).with_for_update())
            if provider_calls == 2:
                evidence_id = payload["evidence"][0]["evidence_id"]
                await session.execute(
                    update(KnowledgeSnippet)
                    .where(KnowledgeSnippet.tenant_id == tenant_id, KnowledgeSnippet.id == evidence_id)
                    .values(excerpt="Changed while provider was running.")
                )
        evidence = payload["evidence"][0]
        return {
            "content": json.dumps(
                {"paragraphs": [{"text": evidence["excerpt"], "evidence_refs": [evidence["ref"]]}]},
                ensure_ascii=False,
            )
        }

    monkeypatch.setattr(llm_client, "chat_with_fallback", lock_probe)

    request = {
        "title": "Письмо",
        "document_type": "letter",
        "data_boundary": "cloud",
        "case_evidence_id": case_id,
    }
    with scope(world):
        first = await drafting_service.draft(request)
        assert first.status == "draft"
        async with async_session_maker() as session:
            count_after_first = await session.scalar(
                select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.tenant_id == tenant_id)
            )

        with pytest.raises(DocumentConflict, match="document_draft_context_changed"):
            await drafting_service.draft(request)
        async with async_session_maker() as session:
            count_after_change = await session.scalar(
                select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.tenant_id == tenant_id)
            )
        assert count_after_change == count_after_first
        assert provider_calls == 2
