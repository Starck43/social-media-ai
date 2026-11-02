"""Explicit test-only evidence for isolated template and router unit tests."""

from types import ModuleType


def evidence_row(kind="knowledge"):
    return {
        "id": 11,
        "context_id": "11",
        "context_type": kind,
        "title": "Stored test evidence",
        "source_ref": "fixture://source/11",
        "citation_anchor": "page 2",
        "revision_hash": "sha256:fixture",
        "data_boundary": "cloud",
        "acl_scope": "collection.source.view",
        "excerpt": "Test-only evidence text",
        "extraction_limit_note": "Fixture extraction",
        "chat_url": f"/app/chat?context_type={kind}&context_id=11&boundary=cloud",
        "version": 1,
        "status": "Черновик",
        "approved_by": None,
        "counterparty": "Fixture counterparty",
        "unsupported_terms_note": "",
    }


def workspace_double():
    module = ModuleType("app.services.knowledge.workspace")

    async def snippets(kind="knowledge", boundary="cloud", query=""):
        return [evidence_row(kind)]

    async def documents(boundary="cloud"):
        return [evidence_row("document")]

    async def resolve(kind, object_id, boundary):
        row = evidence_row(kind)
        row.update(
            type=kind,
            id=object_id,
            context_id=object_id,
            boundary=boundary,
            data_boundary=boundary,
            is_local_only=boundary == "local_only",
            ref=row["source_ref"],
        )
        return row

    module.snippet_rows, module.document_rows, module.resolve_context = snippets, documents, resolve
    return module
