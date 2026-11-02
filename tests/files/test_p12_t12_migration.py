"""Reviewed 0124 round trip in test_schema only; preserve legacy 0122 evidence."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, inspect, select

from app.core.config import settings
from app.core.database import async_session_maker, engine
from app.models import DocumentRevision, FileCatalogObject, KnowledgeDocument, KnowledgeSnippet, Tenant

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
            command.upgrade(config, "0124")
            assert "file_catalog_objects" in inspect(connection).get_table_names(schema="test_schema")
        async with async_session_maker() as session:
            assert (await session.get(KnowledgeSnippet, ids[1])).excerpt == "Synthetic legacy evidence"
            assert (await session.get(KnowledgeDocument, ids[2])).title == "Legacy document"
            kept = await session.get(DocumentRevision, ids[3])
            assert kept.body_text == body and kept.content_hash == revision.content_hash and kept.version == 1
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(Tenant).where(Tenant.id == ids[0]))
