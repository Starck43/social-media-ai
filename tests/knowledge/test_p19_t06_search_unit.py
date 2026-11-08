"""DB-exempt SQL contract tests for metadata-only document search."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services.knowledge import search as mod
from app.types import ActionType

__source_only__ = True

IDENTITY = (9, 22, "owner", ("collection.source.view", "public"))


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def all(self):
        return self.rows


class Session:
    def __init__(self, rows=()):
        self.result = Result(rows)
        self.statement = None
        self.execute_calls = 0

    async def execute(self, statement):
        self.execute_calls += 1
        self.statement = statement
        return self.result


def compiled(session):
    return session.statement.compile(dialect=postgresql.dialect())


def flattened_params(statement):
    def flatten(value):
        if isinstance(value, dict):
            for nested in value.values():
                yield from flatten(nested)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for nested in value:
                yield from flatten(nested)
        else:
            yield value

    return list(flatten(statement.params))


@pytest.fixture
def search_setup(monkeypatch):
    service = mod.DocumentSearchService()
    session = Session()
    monkeypatch.setattr(mod.document_service, "_actor", AsyncMock(return_value=IDENTITY))
    monkeypatch.setattr(mod.document_service, "_output", AsyncMock(return_value="safe-output"))
    monkeypatch.setattr(
        "app.core.access_catalog.workspace_grants",
        lambda role: {("contact", ActionType.VIEW)},
    )
    monkeypatch.setattr("app.core.permissions.get_current_user", lambda: object())
    monkeypatch.setattr("app.core.permissions.has_permission", lambda *args: True)
    return service, session


@pytest.mark.asyncio
async def test_sql_scopes_workspace_acl_boundary_and_current_revision_before_pagination(search_setup):
    service, session = search_setup
    rows = [(SimpleNamespace(id=8), SimpleNamespace(id=80))]
    session.result = Result(rows)

    result = await service.search(q="memo", boundary="company", limit=3, session=session)

    sql = str(compiled(session))
    normalized_sql = sql.replace('"', "").replace("test_schema.", "")
    assert result == {"items": ["safe-output"], "limit": 3, "next_before_id": None}
    assert "knowledge_documents.tenant_id" in sql
    assert "knowledge_documents.acl_scope" in sql
    assert "knowledge_documents.data_boundary" in sql
    assert "document_revisions.id = knowledge_documents.current_revision_id" in normalized_sql
    assert "document_revisions.tenant_id = knowledge_documents.tenant_id" in normalized_sql
    where_clause = sql.partition("WHERE ")[2]
    assert where_clause and "document_revisions.body_text" not in where_clause
    assert "LIMIT" in normalized_sql and "ORDER BY knowledge_documents.id DESC" in normalized_sql
    params = compiled(session).params
    values = flattened_params(compiled(session))
    assert 9 in values
    assert "company" in values or "cloud" in values
    assert "collection.source.view" in str(values)
    assert mod.document_service._output.await_args.args[-1] == IDENTITY


@pytest.mark.asyncio
async def test_filters_bind_current_revision_metadata_and_visible_case_scope(search_setup):
    service, session = search_setup
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 1, tzinfo=timezone.utc)

    await service.search(
        document_type="memo",
        status="draft",
        tag="tax",
        contact_id=31,
        case_evidence_id=41,
        author="Alex",
        revision_from=start,
        revision_to=end,
        session=session,
    )

    sql = str(compiled(session))
    normalized_sql = sql.replace('"', "").replace("test_schema.", "")
    params = compiled(session).params
    assert "knowledge_documents.document_type" in sql
    assert "document_revisions.status IN" in normalized_sql
    assert "knowledge_documents.tags @>" in sql
    assert "knowledge_documents.contact_id" in sql
    assert "knowledge_documents.case_evidence_id" in sql
    assert "document_revisions.author ILIKE" in sql
    assert "document_revisions.created_at" in sql
    assert "knowledge_snippets.tenant_id" in sql
    assert "knowledge_snippets.acl_scope" in sql
    assert "knowledge_snippets.data_boundary" in sql
    assert "knowledge_snippets.context_type" in sql
    values = flattened_params(compiled(session))
    assert all(value in values for value in ("memo", "draft", "draft_invalidated", 31, 41, start, end))


@pytest.mark.asyncio
async def test_contact_filter_fails_closed_without_contact_view(search_setup, monkeypatch):
    service, session = search_setup
    monkeypatch.setattr("app.core.access_catalog.workspace_grants", lambda role: set())

    result = await service.search(contact_id=31, session=session)

    assert result == {"items": [], "limit": 50, "next_before_id": None}
    assert session.execute_calls == 0


@pytest.mark.asyncio
async def test_search_escapes_wildcards_and_carries_cursor(search_setup):
    service, session = search_setup
    session.result = Result(
        [(SimpleNamespace(id=12), SimpleNamespace(id=120)), (SimpleNamespace(id=11), SimpleNamespace(id=110))]
    )

    result = await service.search(q="50%_\\off", before_id=20, limit=1, session=session)

    sql = str(compiled(session))
    params = compiled(session).params
    assert result["items"] == ["safe-output"]
    assert result["next_before_id"] == 12
    assert "knowledge_documents.id <" in sql
    assert "ESCAPE" in sql
    assert any("50\\%\\_\\\\off" in str(value) for value in params.values())


@pytest.mark.asyncio
async def test_invalid_naive_revision_date_is_rejected_before_query(search_setup):
    service, session = search_setup

    with pytest.raises(ValueError, match="invalid_search"):
        await service.search(revision_from=datetime(2026, 1, 1), session=session)

    assert session.execute_calls == 0
