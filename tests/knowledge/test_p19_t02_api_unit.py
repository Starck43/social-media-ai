"""DB-exempt HTTP contract tests for `POST /api/v1/documents/draft` and `/api/v1/documents/templates` (P19-T02)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.api.v1.endpoints import documents as api
from app.services.knowledge.document_contracts import TemplateOut
from app.services.knowledge.documents import DocumentConflict, DocumentUnavailable
from app.services.knowledge.drafting import (
    HUMAN_REVIEW_MARKER,
    UNSUPPORTED_MARKER,
    DocumentDraftOut,
    DraftParagraphOut,
    EvidenceRefOut,
)

__source_only__ = True


@pytest.fixture(autouse=True)
def no_db():
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden")

    event.listen(Dialect, "do_connect", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


def _app_with_auth_doubles():
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")

    async def allow():
        return None

    for route in api.router.routes:
        if not hasattr(route, "dependant"):
            continue
        for dependency in route.dependant.dependencies:
            app.dependency_overrides[dependency.call] = allow
    return app


def _draft_out() -> DocumentDraftOut:
    return DocumentDraftOut(
        id=25,
        title="Ответ на претензию ООО Вектор",
        document_type="claim_response",
        body_text=(
            "Поставка выполнена в срок [Источники: case:4]\n\n"
            f"> **{UNSUPPORTED_MARKER}** Выплата штрафа 50 000 руб. {HUMAN_REVIEW_MARKER}"
        ),
        counterparty="ООО Вектор",
        tags=["claim"],
        contact_id=3,
        case_evidence_id=4,
        owner_user_id=2,
        data_boundary="cloud",
        change_description="Черновик создан через AI/шаблон (P19-T02)",
        current_revision_id=26,
        version=1,
        content_hash="sha256:" + "b" * 64,
        status="draft",
        approval_invalidated=False,
        approved_by=None,
        decision_actor_user_id=None,
        author_user_id=2,
        updated_at=datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc),
        template_id=7,
        rendered_variables={"counterparty": "ООО Вектор"},
        paragraphs=[
            DraftParagraphOut(
                index=1,
                text="Поставка выполнена в срок",
                rendered_text="Поставка выполнена в срок [Источники: case:4]",
                evidence_refs=["case:4"],
                supported=True,
                unsupported_claims=[],
                highlighted=False,
                requires_human_review=True,
                review_reasons=["document_type:claim_response"],
            ),
            DraftParagraphOut(
                index=2,
                text="Выплата штрафа 50 000 руб.",
                rendered_text=f"> **{UNSUPPORTED_MARKER}** Выплата штрафа 50 000 руб. {HUMAN_REVIEW_MARKER}",
                evidence_refs=[],
                supported=False,
                unsupported_claims=["Выплата штрафа 50 000 руб."],
                highlighted=True,
                requires_human_review=True,
                review_reasons=["financial_amount", "financial_penalty", "unsupported_statement"],
            ),
        ],
        evidence=[
            EvidenceRefOut(
                ref="case:4",
                evidence_id=4,
                context_type="case",
                title="Акт приёмки",
                citation_anchor="case:4#p1",
                revision_hash="sha256:1234",
                excerpt="Поставка выполнена в срок",
                data_boundary="cloud",
            )
        ],
        unsupported_claims=["Выплата штрафа 50 000 руб."],
        has_unsupported_claims=True,
        requires_human_review=True,
        review_reasons=[
            "document_type:claim_response",
            "financial_amount",
            "financial_penalty",
            "unsupported_statement",
        ],
        human_review_notice="Черновик требует обязательной проверки человеком перед согласованием",
        unsupported_terms_note="Неподтверждённые утверждения (1): Выплата штрафа 50 000 руб.",
    )


@pytest.mark.asyncio
async def test_post_documents_draft_and_error_mapping(monkeypatch):
    monkeypatch.setattr(api.drafting_service, "draft", AsyncMock(return_value=_draft_out()))
    async with AsyncClient(transport=ASGITransport(app=_app_with_auth_doubles()), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/documents/draft",
            json={
                "title": "Ответ на претензию ООО Вектор",
                "document_type": "claim_response",
                "template_id": 7,
                "variables": {"counterparty": "ООО Вектор"},
                "contact_id": 3,
                "case_evidence_id": 4,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] == 25
        assert body["status"] == "draft"
        assert body["approved_by"] is None
        assert body["has_unsupported_claims"] is True
        assert body["requires_human_review"] is True
        assert len(body["paragraphs"]) == 2
        assert body["paragraphs"][0]["supported"] is True
        assert body["paragraphs"][1]["supported"] is False
        assert body["paragraphs"][1]["highlighted"] is True

        # Missing required template variable -> ValueError -> 422 without leaking internal stack
        api.drafting_service.draft.side_effect = ValueError("missing_template_variables:amount")
        resp_missing = await client.post(
            "/api/v1/documents/draft",
            json={"title": "Письмо", "document_type": "letter", "template_id": 7, "variables": {}},
        )
        assert resp_missing.status_code == 422

        # Missing/foreign reference -> DocumentUnavailable -> 404 without leaking secret message
        api.drafting_service.draft.side_effect = DocumentUnavailable("internal_secret_ref")
        resp_404 = await client.post(
            "/api/v1/documents/draft",
            json={"title": "Письмо", "document_type": "letter", "case_evidence_id": 999},
        )
        assert resp_404.status_code == 404
        assert "internal_secret_ref" not in resp_404.text

        # Missing permission -> PermissionError -> 403
        api.drafting_service.draft.side_effect = PermissionError("secret_role")
        resp_403 = await client.post(
            "/api/v1/documents/draft",
            json={"title": "Письмо", "document_type": "letter", "contact_id": 3},
        )
        assert resp_403.status_code == 403
        assert "secret_role" not in resp_403.text

        # Conflict -> DocumentConflict -> 409
        api.drafting_service.draft.side_effect = DocumentConflict("conflict_secret")
        resp_409 = await client.post(
            "/api/v1/documents/draft",
            json={"title": "Письмо", "document_type": "letter"},
        )
        assert resp_409.status_code == 409
        assert "conflict_secret" not in resp_409.text


@pytest.mark.asyncio
async def test_get_and_post_templates_endpoints(monkeypatch):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    tpl = TemplateOut(
        id=7,
        name="Претензионное письмо",
        document_type="claim_response",
        content="Уважаемый {{ counterparty }}! Сумма: {{ amount }} руб.",
        variables_schema={"counterparty": "string", "amount": "number"},
        updated_at=now,
    )
    monkeypatch.setattr(
        api.document_service,
        "list_templates",
        AsyncMock(return_value={"items": [tpl], "limit": 50, "next_before_id": None}),
    )
    monkeypatch.setattr(api.document_service, "create_template", AsyncMock(return_value=tpl))

    async with AsyncClient(transport=ASGITransport(app=_app_with_auth_doubles()), base_url="http://test") as client:
        get_resp = await client.get("/api/v1/documents/templates")
        assert get_resp.status_code == 200
        assert get_resp.json()["items"][0]["id"] == 7

        post_resp = await client.post(
            "/api/v1/documents/templates",
            json={
                "name": "Претензионное письмо",
                "document_type": "claim_response",
                "content": "Уважаемый {{ counterparty }}! Сумма: {{ amount }} руб.",
                "variables_schema": {"counterparty": "string", "amount": "number"},
            },
        )
        assert post_resp.status_code == 201
        assert post_resp.json()["variables_schema"] == {"counterparty": "string", "amount": "number"}
