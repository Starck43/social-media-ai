"""Catalog migrations in test_schema only; preserve legacy 0122 evidence."""

from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import delete, func, inspect, select, text

from app.core.config import settings
from app.core.database import async_session_maker, engine
from app.models import DocumentRevision, FileCatalogObject, KnowledgeDocument, KnowledgeSnippet, Tenant
from app.models.managers.file_catalog_manager import FileCatalogManager
from tests.shared.file_catalog_fixtures import actor, admit, data  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = pytest.mark.tenancy


SCHEMA = "test_schema"
PRIVATE_TABLES = (
    "file_insight_mounts",
    "file_insight_object_states",
    "file_insight_attempts",
    "file_private_insights",
)


def _alembic_config():
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
    return config


def _file_revision(config, filename):
    revisions = [
        revision
        for revision in ScriptDirectory.from_config(config).walk_revisions()
        if Path(revision.path).name.endswith(filename)
    ]
    assert len(revisions) == 1, f"expected one Alembic migration file named {filename}"
    return revisions[0]


def _current_revision(connection):
    revisions = connection.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version")).scalars().all()
    assert len(revisions) == 1, "migration tests require one Alembic head"
    return revisions[0]


def _require_empty_private_storage(connection):
    if "file_insight_mounts" not in inspect(connection).get_table_names(schema=SCHEMA):
        return
    for table in PRIVATE_TABLES:
        count = connection.execute(text(f"SELECT count(*) FROM {SCHEMA}.{table}")).scalar_one()
        assert count == 0, "refusing a destructive migration test while private insight state exists"


@contextmanager
def _downgraded(config, target):
    """Exercise a historical schema while restoring the exact starting head."""
    script = ScriptDirectory.from_config(config)
    starting_head = script.get_current_head()
    with engine.connect() as connection:
        starting_revision = _current_revision(connection)
        assert starting_revision == starting_head, "migration tests require the branch's current Alembic head"
        if any(revision.revision == "0128" for revision in script.iterate_revisions(starting_revision, target)):
            _require_empty_private_storage(connection)
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, target)
            yield connection
    finally:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, starting_revision)
            assert (
                _current_revision(connection) == starting_revision
            ), "migration test failed to restore its starting head"


async def test_round_trip_preserves_0122_evidence():
    assert settings.DB_SCHEMA == SCHEMA
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
        config = _alembic_config()
        catalog_revision = _file_revision(config, "file_metadata_catalog.py")
        traversal_revision = _file_revision(config, "0127_file_catalog_traversal.py")
        # Stop before this feature's first revision, preserving migrations from dependencies.
        with _downgraded(config, catalog_revision.down_revision) as connection:
            assert "file_catalog_objects" not in inspect(connection).get_table_names(schema="test_schema")
            command.upgrade(config, traversal_revision.revision)
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
    require_isolated_schema()
    manager = FileCatalogManager()
    with actor(data) as args:
        names = [f"Документ-{i:03}.txt" for i in range(501)] + ["Straße.txt", "CAFÉ.txt"]
        for start in range(0, len(names), 100):
            await admit(args, *names[start : start + 100])
        original = await manager.list_page(**args, query="strasse")
        original_id = original["items"][0]["id"]
        config = _alembic_config()
        catalog_revision = _file_revision(config, "file_metadata_catalog.py")
        search_revision = _file_revision(config, "0125_file_catalog_search_key.py")
        with _downgraded(config, catalog_revision.revision) as connection:
            assert "name_search" not in {
                c["name"] for c in inspect(connection).get_columns("file_catalog_objects", schema=SCHEMA)
            }
            command.upgrade(config, search_revision.revision)
            rows = connection.execute(
                text(f"SELECT name, name_search FROM {SCHEMA}.file_catalog_objects WHERE connection_id = :id"),
                {"id": args["connection_id"]},
            ).all()
            assert len(rows) == len(names)
            assert dict(rows)["Straße.txt"] == "strasse.txt"
        result = await manager.list_page(**args, query="STRASSE")
        assert result["items"][0]["id"] == original_id
        assert [x["name"] for x in (await manager.list_page(**args, query="cafe\u0301"))["items"]] == ["CAFÉ.txt"]


async def test_0126_round_trip_keeps_catalog_objects(data):
    require_isolated_schema()
    from app.services.file_workspace.catalog_scan import start_catalog_scan

    manager = FileCatalogManager()
    with actor(data) as args:
        await admit(args, "retained.txt")
        await start_catalog_scan(**args, folder="/work")
        before = (await manager.list_page(**args))["items"][0]["id"]
        config = _alembic_config()
        traversal_revision = _file_revision(config, "0127_file_catalog_traversal.py")
        scans_revision = _file_revision(config, "0126_file_catalog_scans.py")
        with _downgraded(config, scans_revision.down_revision) as connection:
            assert "file_catalog_scans" not in inspect(connection).get_table_names(schema=SCHEMA)
            command.upgrade(config, traversal_revision.revision)
            assert "file_catalog_scans" in inspect(connection).get_table_names(schema=SCHEMA)
        assert (await manager.list_page(**args))["items"][0]["id"] == before


async def test_0127_downgrade_discards_recursive_only_and_keeps_metadata(data):
    require_isolated_schema()
    from app.models import FileCatalogScan
    from app.services.file_workspace.catalog_scan import start_catalog_scan

    manager = FileCatalogManager()
    with actor(data) as args:
        await admit(args, "retained-recursive.txt")
        await start_catalog_scan(**args, folder="/work", recursive=True)
        before = (await manager.list_page(**args))["items"][0]["id"]
    with actor(data, user=1, connection=1) as other:
        await start_catalog_scan(**other, folder="/work")
    config = _alembic_config()
    scans_revision = _file_revision(config, "0126_file_catalog_scans.py")
    traversal_revision = _file_revision(config, "0127_file_catalog_traversal.py")
    with _downgraded(config, traversal_revision.down_revision) as connection:
        assert "traversal" not in {
            c["name"] for c in inspect(connection).get_columns("file_catalog_scans", schema=SCHEMA)
        }
        assert connection.execute(
            text(f"SELECT connection_id FROM {SCHEMA}.file_catalog_scans ORDER BY connection_id")
        ).scalars().all() == [data.connections[1]]
        command.upgrade(config, traversal_revision.revision)
    async with async_session_maker() as session:
        kept = (await session.scalars(select(FileCatalogScan))).all()
        assert len(kept) == 1 and kept[0].connection_id == data.connections[1] and kept[0].traversal is None
    with actor(data) as args:
        assert (await manager.list_page(**args))["items"][0]["id"] == before
