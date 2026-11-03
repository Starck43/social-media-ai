"""Catalog migrations in test_schema only; preserve legacy 0122 evidence."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, inspect, select, text

from app.core.config import settings
from app.core.database import async_session_maker, engine
from app.models import DocumentRevision, FileCatalogObject, KnowledgeDocument, KnowledgeSnippet, Tenant
from app.models.managers.file_catalog_manager import FileCatalogManager
from tests.shared.file_catalog_fixtures import actor, admit, data  # noqa: F401

pytestmark = pytest.mark.tenancy


async def test_round_trip_preserves_0122_evidence():
    assert settings.DB_SCHEMA == "test_schema"
    async with async_session_maker() as session, session.begin():
        assert await session.scalar(select(func.count()).select_from(FileCatalogObject)) == 0
        tenant = Tenant(name="P12-T12 migration fixture", slug="p12t12-migration-fixture")
        session.add(tenant)
        await session.flush()
        snippet = KnowledgeSnippet(
            tenant_id=tenant.id,
            title="Legacy snippet",
            source_ref="synthetic:legacy",
            citation_anchor="line 1",
            data_boundary="cloud",
            acl_scope="collection.source.view",
            context_type="knowledge",
            revision_hash="metadata-only",
            excerpt="Synthetic legacy evidence",
            extraction_limit_note="",
        )
        doc = KnowledgeDocument(
            tenant_id=tenant.id,
            title="Legacy document",
            source_ref="synthetic:legacy",
            citation_anchor="line 1",
            data_boundary="cloud",
            acl_scope="collection.source.view",
            counterparty="Synthetic",
        )
        session.add_all([snippet, doc])
        await session.flush()
        from hashlib import sha256

        body = "Synthetic immutable legacy revision"
        revision = DocumentRevision(
            tenant_id=tenant.id,
            document_id=doc.id,
            version=1,
            content_hash="sha256:" + sha256(body.encode()).hexdigest(),
            body_text=body,
            status="draft",
            author="Synthetic",
            unsupported_terms_note="",
        )
        session.add(revision)
        await session.flush()
        ids = tenant.id, snippet.id, doc.id, revision.id
    try:
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
        # A synchronous injected connection avoids async-driver Alembic CLI drift.
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0123")
            assert "file_catalog_objects" not in inspect(connection).get_table_names(schema="test_schema")
            command.upgrade(config, "0125")
            assert "file_catalog_objects" in inspect(connection).get_table_names(schema="test_schema")
        async with async_session_maker() as session:
            assert (await session.get(KnowledgeSnippet, ids[1])).excerpt == "Synthetic legacy evidence"
            assert (await session.get(KnowledgeDocument, ids[2])).title == "Legacy document"
            kept = await session.get(DocumentRevision, ids[3])
            assert kept.body_text == body and kept.content_hash == revision.content_hash and kept.version == 1
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(Tenant).where(Tenant.id == ids[0]))


async def test_0125_backfills_existing_rows_in_batches(data):
    assert settings.DB_SCHEMA == "test_schema"
    manager = FileCatalogManager()
    with actor(data) as args:
        names = [f"Документ-{i:03}.txt" for i in range(501)] + ["Straße.txt", "CAFÉ.txt"]
        for start in range(0, len(names), 100):
            await admit(args, *names[start : start + 100])
        original = await manager.list_page(**args, query="strasse")
        original_id = original["items"][0]["id"]
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
        try:
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "0124")
                assert "name_search" not in {
                    c["name"] for c in inspect(connection).get_columns("file_catalog_objects", schema="test_schema")
                }
                command.upgrade(config, "0125")
                rows = connection.execute(
                    text("SELECT name, name_search FROM test_schema.file_catalog_objects WHERE connection_id = :id"),
                    {"id": args["connection_id"]},
                ).all()
                assert len(rows) == len(names)
                assert dict(rows)["Straße.txt"] == "strasse.txt"
        finally:
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0125")
        result = await manager.list_page(**args, query="STRASSE")
        assert result["items"][0]["id"] == original_id
        assert [x["name"] for x in (await manager.list_page(**args, query="cafe\u0301"))["items"]] == ["CAFÉ.txt"]
