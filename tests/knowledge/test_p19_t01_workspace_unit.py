"""DB-exempt regression checks for existing evidence and document handoff."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.services.knowledge import workspace as ws


@pytest.fixture(autouse=True)
def no_db():
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden")

    event.listen(Dialect, "do_connect", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    async def order_by(self, *args):
        return self.rows

    async def first(self):
        return self.rows[0] if self.rows else None


def snippet(**overrides):
    values = dict(
        id=1,
        context_type="case",
        title="case",
        source_ref="synthetic:case",
        citation_anchor="p1",
        data_boundary="cloud",
        acl_scope="public",
        revision_hash="sha256:case",
        excerpt="case text",
        extraction_limit_note="",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def document():
    return SimpleNamespace(
        id=2,
        title="doc",
        source_ref="synthetic:doc",
        citation_anchor="p2",
        data_boundary="cloud",
        acl_scope="public",
        counterparty="customer",
    )


def revision(**overrides):
    values = dict(
        id=3,
        document_id=2,
        version=1,
        body_text="document body",
        content_hash="sha256:doc",
        status="draft_invalidated",
        approved_by=None,
        unsupported_terms_note="warning",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_catalog_uses_latest_revision_and_snippet_query(monkeypatch):
    row, doc = snippet(), document()
    first, latest = revision(), revision(id=4, version=2, status="in_review")
    monkeypatch.setattr(ws, "current_tenant_id", lambda: 7)
    monkeypatch.setattr(ws.KnowledgeSnippet, "objects", SimpleNamespace(visible=lambda *args, **kwargs: Rows([row])))
    monkeypatch.setattr(ws.KnowledgeDocument, "objects", SimpleNamespace(visible=lambda *args, **kwargs: Rows([doc])))
    monkeypatch.setattr(
        ws.DocumentRevision, "objects", SimpleNamespace(filter=lambda *args, **kwargs: Rows([latest, first]))
    )
    assert len(await ws.snippet_rows("case", "cloud", query="CASE TEXT")) == 1
    assert await ws.snippet_rows("case", "cloud", query="absent") == []
    catalog = await ws.document_rows("cloud")
    assert catalog[0]["context_id"] == "4" and catalog[0]["status"] == "На согласовании"
    assert catalog[0]["unsupported_terms_note"] == "warning"
    monkeypatch.setattr(ws.KnowledgeDocument, "objects", SimpleNamespace(visible=lambda *args, **kwargs: Rows([])))
    assert await ws.document_rows() == []


@pytest.mark.asyncio
async def test_handoff_validates_selector_boundary_and_visibility(monkeypatch):
    row, doc, rev = snippet(), document(), revision()
    monkeypatch.setattr(ws.DocumentRevision, "objects", SimpleNamespace(visible=lambda *args, **kwargs: Rows([rev])))
    monkeypatch.setattr(ws.KnowledgeDocument, "objects", SimpleNamespace(visible=lambda *args, **kwargs: Rows([doc])))
    monkeypatch.setattr(ws.KnowledgeSnippet, "objects", SimpleNamespace(visible=lambda *args, **kwargs: Rows([row])))
    resolved = await ws.resolve_context("document", "3", "cloud")
    assert resolved["context_id"] == "3" and resolved["ref"] == "synthetic:doc"
    assert (await ws.resolve_context("case", "1", "cloud"))["excerpt"] == "case text"
    for kind, object_id, boundary, status in [
        ("invalid", "1", "cloud", 400),
        ("case", "0", "cloud", 404),
        ("case", "x", "cloud", 404),
        ("case", "1", "company", 403),
    ]:
        with pytest.raises(HTTPException) as exc:
            await ws.resolve_context(kind, object_id, boundary)
        assert exc.value.status_code == status
    monkeypatch.setattr(ws.KnowledgeSnippet, "objects", SimpleNamespace(visible=lambda *args, **kwargs: Rows([])))
    with pytest.raises(HTTPException) as exc:
        await ws.resolve_context("case", "1", "cloud")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_unified_message_handoff_fails_closed_and_redacts_unavailable(monkeypatch):
    monkeypatch.setattr(ws, "current_tenant_id", lambda: 7)
    monkeypatch.setattr(ws, "get_current_user", lambda: object())
    monkeypatch.setattr(ws, "has_permission", lambda *args: False)
    assert await ws._resolve_unified_message_context(1, "cloud") is None
    monkeypatch.setattr(ws, "has_permission", lambda *args: True)
    msg = SimpleNamespace(
        id=1,
        tenant_id=7,
        is_hidden=False,
        data_boundary="cloud",
        subject_encrypted="test",
        sender_encrypted="a",
        recipient_encrypted="b",
        channel="email",
        original_id="m1",
        thread_id=1,
        unavailable_at=None,
        original_url="https://example.invalid",
        content_hash="sha256:test",
    )
    monkeypatch.setattr(ws.UnifiedMessage, "objects", SimpleNamespace(filter=lambda *args, **kwargs: Rows([msg])))
    out = await ws._resolve_unified_message_context(1, "cloud")
    assert out["href"] == "https://example.invalid" and out["context_type"] == "communication"
    msg.unavailable_at = object()
    assert (await ws._resolve_unified_message_context(1, "cloud"))["href"] is None
    msg.tenant_id = 8
    assert await ws._resolve_unified_message_context(1, "cloud") is None
    msg.tenant_id, msg.data_boundary = 7, "local_only"
    assert await ws._resolve_unified_message_context(1, "cloud") is None
    monkeypatch.setattr(ws, "current_tenant_id", lambda: None)
    assert await ws._resolve_unified_message_context(1, "cloud") is None
