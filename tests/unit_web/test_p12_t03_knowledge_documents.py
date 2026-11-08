"""Isolated unit tests for P12-T03 Knowledge & Documents workspace.

Covers:
- T03.1: ACL-aware retrieval and boundary isolation (local_only | company | cloud)
- T03.2: Versioned documents, content hash tracking, and approval invalidation
- T03.3: FastAPI domain routers and StrictUndefined Jinja2 template rendering
- Zero-Conflict verification: app/web/nav.py and app/web/__init__.py untouched
"""

from __future__ import annotations

import types
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from tests.shared.evidence_fixtures import evidence_row, workspace_double
from tests.source_import_isolation import load_isolated_source

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]


def _load_knowledge_retrieval():
    return load_isolated_source(
        "_p12_t03_retrieval",
        ROOT / "app/services/knowledge/retrieval.py",
    )


def _load_knowledge_documents():
    return load_isolated_source(
        "_p12_t03_documents",
        ROOT / "app/services/knowledge/documents.py",
    )


def _load_knowledge_service():
    retrieval = _load_knowledge_retrieval()
    documents = _load_knowledge_documents()
    return load_isolated_source(
        "_p12_t03_knowledge_pkg",
        ROOT / "app/services/knowledge/__init__.py",
        {
            "app.services.knowledge.retrieval": retrieval,
            "app.services.knowledge.documents": documents,
        },
    )


def _build_test_jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates"),
        autoescape=select_autoescape(),
        undefined=StrictUndefined,
        extensions=["jinja2.ext.do"],
    )
    env.filters["ui_label"] = str
    env.filters["human_dt"] = lambda val, **kw: str(val or "")
    env.globals["static_v"] = lambda path: "http://fixture.test/static/" + path
    env.globals["url_for"] = lambda name, path: "http://fixture.test/static/" + path
    return env


def _build_base_template_context() -> dict:
    perms = NS(
        can=lambda *args: True,
        can_manage_workspace=lambda *a: False,
        can_open_chat=False,
        is_owner=False,
        is_superuser_role=False,
        workspace_role="viewer",
    )
    return {
        "section": "knowledge",
        "tab": "",
        "tenant": NS(id=1, name="ООО Тест", slug="test"),
        "user": NS(id=42, username="saloon.as", email="saloon.as@fixture.test", has_admin_access=lambda: False),
        "workspaces": [],
        "memberships": [],
        "unread_notifications": 0,
        "nav": [
            NS(key="dashboard", href="/app/", label="Дашборд", ready=True, icon="home"),
            NS(key="knowledge", href="#", label="База знаний", ready=False, icon="book"),
            NS(key="documents", href="#", label="Документы", ready=False, icon="file"),
        ],
        "csrf": "fixture-token",
        "messages": [],
        "flashes": [],
        "request": NS(query_params={}),
        "perms": perms,
        "is_superuser": False,
        "member_level": None,
    }


def test_retrieval_acl_filtering_and_data_boundary_isolation():
    """T03.1: Verify ACL permission filtering and data boundary enforcement."""
    retrieval = _load_knowledge_retrieval()

    test_pool = [
        retrieval.build_knowledge_snippet(
            snippet_id="s1",
            title="Local Gateway Contract",
            source_name="Local Gateway /srv/contract.pdf",
            acl_scope="contracts:legal",
            data_boundary="local_only",
            citation_anchor="p. 1",
            revision_hash="sha256:1111",
            excerpt="Local secret text",
            extraction_limit_note="OCR 0.99",
        ),
        retrieval.build_knowledge_snippet(
            snippet_id="s2",
            title="Company Internal Procurement",
            source_name="Mailbox INBOX",
            acl_scope="procurement:team",
            data_boundary="company",
            citation_anchor="UID 10",
            revision_hash="sha256:2222",
            excerpt="Price table 100",
            extraction_limit_note="XLSX deterministic",
        ),
        retrieval.build_knowledge_snippet(
            snippet_id="s3",
            title="Public Cloud Policy",
            source_name="Yandex Disk /pub/policy.pdf",
            acl_scope="public",
            data_boundary="cloud",
            citation_anchor="Sec. 2",
            revision_hash="sha256:3333",
            excerpt="General policy rules",
            extraction_limit_note="Clean text",
        ),
    ]

    # 1. Anonymous / zero scopes: only public snippet returned
    filtered_no_scopes = retrieval.filter_snippets_by_acl(test_pool, user_scopes=None, allowed_boundary="cloud")
    assert [s["id"] for s in filtered_no_scopes] == ["s3"]

    # 2. User with contracts:legal scope in company boundary:
    filtered_company = retrieval.filter_snippets_by_acl(
        test_pool, user_scopes={"contracts:legal", "procurement:team"}, allowed_boundary="company"
    )
    assert set(s["id"] for s in filtered_company) == {"s2", "s3"}

    # 3. Cloud query with contracts:legal scope only:
    filtered_cloud_legal = retrieval.filter_snippets_by_acl(
        test_pool, user_scopes={"contracts:legal"}, allowed_boundary="cloud"
    )
    assert set(s["id"] for s in filtered_cloud_legal) == {"s3"}
    assert not any(s["id"] == "s2" for s in filtered_cloud_legal)

    # 4. Wildcard scope allows all ACLs within boundary limit
    filtered_wildcard = retrieval.filter_snippets_by_acl(test_pool, user_scopes={"*"}, allowed_boundary="cloud")
    assert [s["id"] for s in filtered_wildcard] == ["s3"]


def test_retrieval_search_query_and_citation_assembly():
    """T03.1: Search matching and LLM citation context formatting."""
    retrieval = _load_knowledge_retrieval()

    # Search by keyword
    fixture = retrieval.build_knowledge_snippet(
        "s1",
        "ДП-2026/114",
        "fixture://contract",
        "public",
        "cloud",
        "page 2",
        "sha256:fixture",
        "валютной",
        "fixture extraction",
    )
    assert retrieval.search_knowledge_snippets("валютной") == []
    results = retrieval.search_knowledge_snippets(
        "валютной", snippets=[fixture], user_scopes={"*"}, allowed_boundary="cloud"
    )
    assert len(results) >= 1
    assert any("ДП-2026/114" in r["title"] for r in results)

    # Citation anchor builder
    anchor = retrieval.build_citation_anchor("pdf", "/srv/doc.pdf", "стр. 5 · п. 2")
    assert anchor == "/srv/doc.pdf · стр. 5 · п. 2"

    # LLM prompt context assembler
    prompt_ctx = retrieval.assemble_llm_citations_context(results)
    assert "ДП-2026/114" in prompt_ctx
    assert "boundary:" in prompt_ctx
    assert "OCR/Extraction note:" in prompt_ctx


def test_document_content_hash_and_versioned_creation():
    """T03.2: Content hash determinism and initial version creation."""
    doc_mod = _load_knowledge_documents()

    hash1 = doc_mod.compute_content_hash("Текст спецификации № 1")
    hash2 = doc_mod.compute_content_hash("Текст спецификации № 1")
    hash3 = doc_mod.compute_content_hash("Текст спецификации № 2")
    assert hash1.startswith("sha256:")
    assert hash1 == hash2
    assert hash1 != hash3

    doc = doc_mod.create_versioned_document(
        title="Спецификация поставки",
        counterparty="ООО Партнер",
        body_text="Объем: 10 тонн",
        author="saloon.as",
    )
    assert doc["version"] == "v1.0"
    assert doc["status_code"] == doc_mod.STATUS_DRAFT
    assert doc["approved_by"] is None
    assert doc["content_hash"] == doc_mod.compute_content_hash("Объем: 10 тонн")
    assert len(doc["revisions"]) == 1


def test_document_edits_invalidate_approval_lifecycle():
    """T03.2: Critical requirement — edits invalidate approval and reset approved_by."""
    doc_mod = _load_knowledge_documents()

    # 1. Create initial draft
    doc = doc_mod.create_versioned_document(
        title="Договор поставки",
        counterparty="ООО Сырье",
        body_text="Базовый текст",
        author="user1",
    )

    # 2. Approve document
    approved_doc = doc_mod.approve_document(doc, approver="saloon.as")
    assert approved_doc["status_code"] == doc_mod.STATUS_APPROVED
    assert approved_doc["approved_by"] == "saloon.as"
    assert "Согласовано (saloon.as)" in approved_doc["status"]

    # 3. Edit document body: MUST invalidate approval!
    edited_doc = doc_mod.update_document_body(approved_doc, new_body_text="Измененный текст поставки", author="user2")
    assert edited_doc["status_code"] == doc_mod.STATUS_DRAFT_INVALIDATED
    assert edited_doc["approved_by"] is None
    assert edited_doc["status"] == doc_mod.STATUS_LABELS[doc_mod.STATUS_DRAFT_INVALIDATED]
    assert edited_doc["content_hash"] == doc_mod.compute_content_hash("Измененный текст поставки")
    assert edited_doc["version"] == "v1.1-edit"
    assert len(edited_doc["revisions"]) == 2

    # 4. Re-approve edited document
    reapproved_doc = doc_mod.approve_document(edited_doc, approver="chief_lawyer")
    assert reapproved_doc["status_code"] == doc_mod.STATUS_APPROVED
    assert reapproved_doc["approved_by"] == "chief_lawyer"


def test_knowledge_jinja_template_strict_rendering():
    """T03.3: StrictUndefined rendering of web/knowledge.html."""
    env = _build_test_jinja_env()
    template = env.get_template("web/knowledge.html")
    context = _build_base_template_context()

    retrieval = _load_knowledge_retrieval()
    context["evidence_rows"] = [evidence_row()]
    context["q"] = "полимерного"
    context["boundary"] = "cloud"

    rendered = template.render(**context)
    assert 'data-p12-knowledge="knowledge-catalog"' in rendered
    assert "База знаний" in rendered
    assert "Stored test evidence" in rendered
    assert "sha256:fixture" in rendered
    assert "collection.source.view" in rendered


def test_documents_jinja_template_strict_rendering():
    """T03.3: StrictUndefined rendering of web/documents.html."""
    env = _build_test_jinja_env()
    template = env.get_template("web/documents.html")
    context = _build_base_template_context()

    doc_mod = _load_knowledge_documents()
    context["evidence_rows"] = [evidence_row("document")]
    context["boundary"] = "cloud"

    rendered = template.render(**context)
    assert 'data-p12-documents="documents-catalog"' in rendered
    assert "Документы и версии" in rendered
    assert "Stored test evidence" in rendered
    assert "sha256:fixture" in rendered
    assert "Fixture counterparty" in rendered
    assert "saloon.as" in rendered


@pytest.mark.asyncio
async def test_fastapi_domain_routers_knowledge_and_documents(monkeypatch):
    """T03.3: Verify FastAPI routers for knowledge and documents return render response."""
    fake_deps = types.ModuleType("app.web.deps")

    rendered_calls = []

    def fake_render(request, template_name, **extra):
        rendered_calls.append((template_name, extra))
        return NS(template=template_name, context=extra, status_code=200)

    fake_deps.render = fake_render

    knowledge_web = load_isolated_source(
        "_p12_t03_web_knowledge",
        ROOT / "app/web/knowledge.py",
        {
            "app.services.knowledge.workspace": workspace_double(),
            "app.web.deps": fake_deps,
        },
    )

    documents_web = load_isolated_source(
        "_p12_t03_web_documents",
        ROOT / "app/web/documents.py",
        {
            "app.services.knowledge.workspace": workspace_double(),
            "app.web.deps": fake_deps,
        },
    )

    req = NS(state=NS(web_user=NS(id=1)))

    # Test knowledge router
    resp_kn = await knowledge_web.knowledge_page(req, q="ДП-2026", boundary="cloud")
    assert resp_kn.status_code == 200
    assert rendered_calls[-1][0] == "web/knowledge.html"
    assert len(rendered_calls[-1][1]["evidence_rows"]) >= 1

    # Test documents router
    resp_doc = await documents_web.documents_page(req, boundary="cloud")
    assert resp_doc.status_code == 200
    assert rendered_calls[-1][0] == "web/documents.html"
    assert rendered_calls[-1][1]["evidence_rows"] == [evidence_row("document")]


def test_knowledge_and_documents_are_registered():
    """The pages must exist in the app, not only as callable handlers.

    This test used to assert the opposite — that the routers were absent from
    `app/web/__init__.py` and the nav entries stayed `ready=False` — which
    locked in a 404 for `/app/knowledge` and `/app/documents`. P12-T09 activated
    them; the application-level check lives in `tests/web/test_p12_routes.py`.
    """
    nav_text = (ROOT / "app/web/nav.py").read_text()
    init_text = (ROOT / "app/web/__init__.py").read_text()

    assert '"/app/knowledge"' in nav_text
    assert '"/app/documents"' in nav_text
    assert "knowledge_router" in init_text
    assert "documents_router" in init_text
