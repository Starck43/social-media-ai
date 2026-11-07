"""DB-exempt migration ordering and constraint tests; not a PostgreSQL cycle."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location(
    "p19_migration", Path(__file__).resolve().parents[2] / "migrations/versions/0142_document_management.py"
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.fixture(autouse=True)
def forbid_db(monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.engine.interfaces import Dialect

    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden in isolated migration unit tests")

    event.listen(Dialect, "do_connect", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


def test_upgrade_widens_constraint_without_rewriting_revision_content(monkeypatch):
    op = Mock()
    op.get_bind.return_value.dialect.identifier_preparer.quote_schema.return_value = '"test_schema"'
    monkeypatch.setattr(migration, "op", op)
    migration.upgrade()
    assert migration.revision == "0142" and migration.down_revision == "0141"
    assert op.drop_constraint.call_args_list[0].args[0] == "ck_document_revision_status"
    sql = op.create_check_constraint.call_args_list[0].args[2]
    for status in ("draft", "draft_invalidated", "in_review", "approved", "rejected", "revision_requested"):
        assert f"'{status}'" in sql


@pytest.mark.parametrize("contains_new_status", [True, False])
def test_downgrade_locks_and_refuses_incompatible_data(monkeypatch, contains_new_status):
    result = SimpleNamespace(scalar_one=lambda: contains_new_status)
    bind = SimpleNamespace(
        dialect=SimpleNamespace(identifier_preparer=SimpleNamespace(quote_schema=lambda value: f'"{value}"')),
        execute=Mock(side_effect=[None, result]),
    )
    op = Mock()
    op.get_bind.return_value = bind
    monkeypatch.setattr(migration, "op", op)
    if contains_new_status:
        with pytest.raises(RuntimeError, match="Downgrade refused"):
            migration.downgrade()
        op.drop_constraint.assert_not_called()
    else:
        migration.downgrade()
        sql = op.create_check_constraint.call_args.args[2]
        assert "'draft_invalidated'" in sql
        assert "'in_review'" not in sql and "'revision_requested'" not in sql
    statements = [str(call.args[0]) for call in bind.execute.call_args_list]
    assert statements[0].startswith("LOCK TABLE ")
    assert "SELECT EXISTS" in statements[1]


def test_orm_constraint_matches_migration_and_translator():
    from sqlalchemy import CheckConstraint

    from app.models.knowledge import DocumentRevision
    from app.services.knowledge.documents import canonical_revision_status

    constraint = next(
        item
        for item in DocumentRevision.__table__.constraints
        if isinstance(item, CheckConstraint) and item.name == "ck_document_revision_status"
    )
    sql = str(constraint.sqltext)
    for status in ("draft", "draft_invalidated", "in_review", "approved", "rejected", "revision_requested"):
        assert f"'{status}'" in sql and f"'{status}'" in migration.NEW_STATUSES
        canonical_revision_status(status)


@pytest.mark.asyncio
async def test_append_keeps_invalidated_status_and_translator_agrees(monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from app.core import database
    from app.models import DocumentRevision
    from app.models.managers import knowledge_manager
    from app.services.knowledge.documents import canonical_revision_status

    doc = SimpleNamespace(id=123)
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: doc)))

    @asynccontextmanager
    async def transaction():
        yield

    @asynccontextmanager
    async def session_factory():
        yield session

    session.begin = transaction
    monkeypatch.setattr(database, "async_session_maker", session_factory)
    monkeypatch.setattr(knowledge_manager, "get_current_user", lambda: object())
    monkeypatch.setattr(knowledge_manager, "has_permission", lambda *args: True)
    monkeypatch.setattr(knowledge_manager, "current_tenant_id", lambda: 7)
    monkeypatch.setattr(knowledge_manager, "permitted_scopes", lambda: ("collection.source.view",))
    manager = knowledge_manager.DocumentRevisionManager(DocumentRevision)
    query = SimpleNamespace(
        order_by=lambda *args: SimpleNamespace(first=AsyncMock(return_value=SimpleNamespace(version=1)))
    )
    monkeypatch.setattr(manager, "filter", lambda **kwargs: query)
    manager.create = AsyncMock(return_value=object())
    await manager.append(123, "edited")
    values = manager.create.await_args.kwargs
    assert values["status"] == "draft_invalidated" and values["approved_by"] is None
    assert f"'{values['status']}'" in migration.NEW_STATUSES
    assert canonical_revision_status(values["status"]) == ("draft", True)


def test_model_metadata_contains_workspace_scoped_links_and_immutable_revision_fields():
    from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

    from app.models.knowledge import DocumentRevision, DocumentTemplate, KnowledgeDocument, KnowledgeSnippet

    doc = KnowledgeDocument.__table__
    assert {"document_type", "tags", "contact_id", "case_id", "owner_user_id", "current_revision_id"} <= set(
        doc.c.keys()
    )
    rev = DocumentRevision.__table__
    assert {"author_user_id", "change_description", "body_text", "content_hash"} <= set(rev.c.keys())
    fk = next(
        c for c in doc.constraints if isinstance(c, ForeignKeyConstraint) and c.name == "fk_document_current_revision"
    )
    assert list(fk.column_keys) == ["tenant_id", "id", "current_revision_id"]
    assert fk.deferrable is True and fk.initially == "DEFERRED"
    for name in ("fk_document_contact_tenant", "fk_document_case_tenant"):
        fk = next(c for c in doc.constraints if isinstance(c, ForeignKeyConstraint) and c.name == name)
        assert list(fk.column_keys)[0] == "tenant_id"
    assert any(
        isinstance(c, UniqueConstraint) and c.name == "uq_knowledge_snippet_tenant"
        for c in KnowledgeSnippet.__table__.constraints
    )
    assert DocumentTemplate.__tenant_scoped__
    assert {"tenant_id", "name", "document_type", "content", "variables_schema"} <= set(
        DocumentTemplate.__table__.c.keys()
    )
