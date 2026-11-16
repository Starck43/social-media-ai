"""P19-T08: the operator CLI surface against an isolated test schema.

The commands run through the same `versions` service, the same workspace and
the same permission check as `/api/v1/documents`: history/diff are reads,
restore appends a revision (and under `--dry-run` writes nothing), and a caller
without document access or outside the workspace is refused. Refusals exit
non-zero with a stable code and print no document content.

A Typer command is synchronous (`asyncio.run` inside), while the test suite
keeps one event loop open for the whole session; the checks below therefore run
the command callbacks in a worker thread under `CliRunner`, which is exactly
what the `documents` entrypoint does for an operator.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack

import pytest
from sqlalchemy import delete, func, select
from typer.testing import CliRunner

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import DocumentRevision, KnowledgeDocument
from app.services.knowledge.documents import document_service
from app.types import ActionType
from cli.commands import documents as documents_cli
from cli.main import app as cli_app
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = pytest.mark.tenancy

runner = CliRunner()

NO_SOURCE_RIGHTS = frozenset({("analysis.result", "view")})


def scoped(world, workspace=0, user=None, grants=None):
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[workspace].id))
    stack.enter_context(
        permission_scope(
            user or world.users[0 if workspace == 0 else 2],
            member_grants=workspace_grants("owner") if grants is None else frozenset(grants),
        )
    )
    return stack


def cli(*argv):
    """Run the `documents` sub-app the way the console script does.

    The worker thread carries the caller's context (workspace, rights), which a
    console process has for its whole lifetime and a test only has inside an
    `ExitStack`.
    """
    import contextvars

    ctx = contextvars.copy_context()
    return asyncio.to_thread(ctx.run, runner.invoke, documents_cli.app, [str(item) for item in argv])


def expected(doc):
    return {"expected_revision_id": doc.current_revision_id, "expected_updated_at": doc.updated_at}


async def create(body):
    return await document_service.create(
        {"title": "synthetic", "document_type": "memo", "body_text": body, "data_boundary": "local_only"}
    )


async def _version_list(document_id):
    from app.services.knowledge.versions import version_service

    return await version_service.list(document_id, boundary="local_only")


async def _version_diff(document_id, revision_id):
    from app.services.knowledge.versions import version_service

    return await version_service.diff(document_id, revision_id, revision_id, boundary="local_only")


async def _version_restore(document_id, revision_id):
    from app.services.knowledge.versions import version_service

    current = await document_service.get(document_id, boundary="local_only")
    data = {"expected_revision_id": current.current_revision_id, "expected_updated_at": current.updated_at}
    return await version_service.restore(document_id, revision_id, data, boundary="local_only")


async def history_versions(document_id):
    async with async_session_maker() as session:
        return (
            await session.scalars(
                select(DocumentRevision.version)
                .where(DocumentRevision.document_id == document_id)
                .order_by(DocumentRevision.version)
            )
        ).all()


@pytest.fixture
async def document_world(personal_world):  # noqa: F811
    assert settings.DB_SCHEMA and settings.DB_SCHEMA != "public"
    ids = [tenant.id for tenant in personal_world.tenants]
    try:
        yield personal_world
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.tenant_id.in_(ids)))
        async with async_session_maker() as session:
            for model in (KnowledgeDocument, DocumentRevision):
                assert (
                    await session.scalar(select(func.count()).select_from(model).where(model.tenant_id.in_(ids))) == 0
                )


def test_documents_sub_app_is_registered_under_main():
    result = runner.invoke(cli_app, ["documents", "--help"])
    assert result.exit_code == 0, result.stderr
    for command in ("history", "diff", "restore"):
        assert command in result.stdout


def test_command_line_is_complete():
    """A command without its workspace or actor never reaches the database."""
    for argv in (
        ["documents", "history", "1"],
        ["documents", "diff", "1", "1", "2"],
        ["documents", "restore", "1", "1"],
    ):
        result = runner.invoke(cli_app, argv)
        assert result.exit_code != 0, argv


async def test_history_and_diff_read_the_service_page(document_world):
    tenant = document_world.tenants[0]
    actor = str(document_world.users[0].id)
    with scoped(document_world):
        doc = await create("first line\n")
        second = await document_service.update(
            doc.id, {**expected(doc), "body_text": "second line\n"}, boundary="local_only"
        )
        first_revision_id, second_revision_id = doc.current_revision_id, second.current_revision_id

    # Same reads as `/documents/{id}/versions` and `/versions/{a}/diff/{b}`.
    page = await documents_cli.history_run(doc.id, tenant.slug, actor)
    assert [revision.version for revision in page["items"]] == [2, 1]
    assert page["current"].current_revision_id == second_revision_id
    assert page["current"].version == 2

    diff = await documents_cli.diff_run(doc.id, first_revision_id, second_revision_id, tenant.slug, actor)
    assert diff.from_revision_id == first_revision_id and diff.to_revision_id == second_revision_id
    assert "".join(part.before for part in diff.parts) == "first line\n"
    assert "".join(part.after for part in diff.parts) == "second line\n"


async def test_history_prints_without_touching_the_document(document_world):
    tenant = document_world.tenants[0]
    actor = str(document_world.users[0].id)
    with scoped(document_world):
        doc = await create("visible body")

    result = await cli("history", doc.id, "--tenant", tenant.slug, "--actor", actor)
    assert result.exit_code == documents_cli.EXIT_OK, result.stderr
    assert str(doc.current_revision_id) in result.stdout
    assert await history_versions(doc.id) == [1]


async def test_restore_dry_run_writes_nothing_then_restore_appends(document_world):
    tenant = document_world.tenants[0]
    actor = str(document_world.users[0].id)
    with scoped(document_world):
        doc = await create("first")
        second = await document_service.update(doc.id, {**expected(doc), "body_text": "second"}, boundary="local_only")
        first_revision_id, second_revision_id = doc.current_revision_id, second.current_revision_id

    dry = await cli("restore", doc.id, first_revision_id, "--tenant", tenant.slug, "--actor", actor, "--dry-run")
    assert dry.exit_code == documents_cli.EXIT_OK, dry.stderr
    assert "nothing written" in dry.stdout and "new version" in dry.stdout
    assert await history_versions(doc.id) == [1, 2]

    preview = await documents_cli.restore_preview(doc.id, first_revision_id, tenant.slug, actor, None)
    assert preview["would_create"] == {
        "version": 3,
        "content_hash": preview["source"].content_hash,
        "body_text": "first",
        "change_description": "Restored revision 1",
        "status": "draft_invalidated",
    }
    assert await history_versions(doc.id) == [1, 2]

    applied = await cli(
        "restore",
        doc.id,
        first_revision_id,
        "--tenant",
        tenant.slug,
        "--actor",
        actor,
        "--description",
        "bring back the first draft",
    )
    assert applied.exit_code == documents_cli.EXIT_OK, applied.stderr
    # History is appended, never rewritten.
    assert await history_versions(doc.id) == [1, 2, 3]
    with scoped(document_world):
        current = await document_service.get(doc.id, boundary="local_only")
    assert current.version == 3 and current.body_text == "first"
    assert current.current_revision_id not in (first_revision_id, second_revision_id)


async def test_restore_preview_of_a_current_revision_creates_nothing(document_world):
    tenant = document_world.tenants[0]
    actor = str(document_world.users[0].id)
    with scoped(document_world):
        doc = await create("first")
    preview = await documents_cli.restore_preview(doc.id, doc.current_revision_id, tenant.slug, actor, None)
    assert preview["current"].version == preview["source"].version == 1
    assert preview["would_create"]["version"] == 2
    assert preview["approval_surface"] == f"/app/documents/manage/{doc.id}?boundary=local_only"
    assert await history_versions(doc.id) == [1]


async def test_restore_without_the_change_right_is_refused(document_world):
    """A viewer may read history but not restore it: the API's own split.

    The endpoint gates reads on `source.view` and restore on `source.update`;
    a workspace viewer role carries only the first. `--actor` resolves the real
    membership, so this test gives the fixture's fourth user that viewer role.
    """
    from app.core.permissions import operator_permission_scope
    from app.models import TenantUser

    tenant = document_world.tenants[0]
    viewer = document_world.users[3]
    with scoped(document_world):
        doc = await create("first")
        await document_service.update(doc.id, {**expected(doc), "body_text": "second"}, boundary="local_only")
        first_revision_id = doc.current_revision_id
    assert ("source", ActionType.VIEW) in workspace_grants("viewer")
    assert ("source", ActionType.UPDATE) not in workspace_grants("viewer")
    with tenant_scope(bypass=True), operator_permission_scope():
        await TenantUser.objects.create(
            tenant_id=tenant.id,
            user_id=viewer.id,
            channel="web",
            external_user_id=str(viewer.id),
            role_id=document_world.roles["viewer"].id,
            is_active=True,
        )
    actor = str(viewer.id)

    result = await cli("history", doc.id, "--tenant", tenant.slug, "--actor", actor)
    assert result.exit_code == documents_cli.EXIT_OK, result.stderr
    assert str(doc.current_revision_id) in result.stdout

    # A preview is a read: the endpoint gates only the POST on `source.update`,
    # so a viewer may see what a restore would create.
    preview = await documents_cli.restore_preview(doc.id, first_revision_id, tenant.slug, actor, None)
    assert preview["would_create"]["version"] == 3
    with pytest.raises(documents_cli.DocumentCommandError) as denied:
        await documents_cli.restore_run(doc.id, first_revision_id, tenant.slug, actor, None)
    assert denied.value.code == "permission_denied"
    assert denied.value.exit_code == documents_cli.EXIT_REFUSED
    assert await history_versions(doc.id) == [1, 2]

    # The write command refuses; `--dry-run` is the same read as above.
    result = await cli("restore", doc.id, first_revision_id, "--tenant", tenant.slug, "--actor", actor)
    assert result.exit_code == documents_cli.EXIT_REFUSED, result.stderr
    assert "permission_denied" in result.stderr
    dry = await cli("restore", doc.id, first_revision_id, "--tenant", tenant.slug, "--actor", actor, "--dry-run")
    assert dry.exit_code == documents_cli.EXIT_OK, dry.stderr
    assert "nothing written" in dry.stdout

    applied = await cli(
        "restore", doc.id, first_revision_id, "--tenant", tenant.slug, "--actor", document_world.users[0].id
    )
    assert applied.exit_code == documents_cli.EXIT_OK, applied.stderr
    assert await history_versions(doc.id) == [1, 2, 3]


async def test_caller_without_document_access_is_refused(document_world):
    """A caller who is not a member of the workspace is refused on every command.

    `--actor` is resolved against the real membership, never against ambient
    grants, so the only unconditional refusal reachable from the entrypoint is
    a user that does not belong to the passed workspace.
    """
    tenant = document_world.tenants[0]
    with scoped(document_world):
        doc = await create("first private body")
    revision_id = doc.current_revision_id
    stranger = str(document_world.users[3].id)

    for argv in (
        ("history", doc.id, "--tenant", tenant.slug, "--actor", stranger),
        ("diff", doc.id, revision_id, revision_id, "--tenant", tenant.slug, "--actor", stranger),
        ("restore", doc.id, revision_id, "--tenant", tenant.slug, "--actor", stranger, "--dry-run"),
        ("restore", doc.id, revision_id, "--tenant", tenant.slug, "--actor", stranger),
    ):
        result = await cli(*argv)
        assert result.exit_code == documents_cli.EXIT_REFUSED, result.stderr
        assert "actor_not_member" in result.stderr
        assert "first private body" not in result.stdout + result.stderr

    # The service's own `source.view` check is the second guard (the endpoint
    # answers 403): every command runs inside the resolved member's scope, so a
    # caller with no document read is refused by the service itself.
    owner = document_world.users[0]
    actor = str(owner.id)
    with scoped(document_world, user=owner, grants=NO_SOURCE_RIGHTS):
        calls = {
            "history": lambda: documents_cli._current(doc.id),
            "list": lambda: _version_list(doc.id),
            "diff": lambda: _version_diff(doc.id, revision_id),
            "restore": lambda: _version_restore(doc.id, revision_id),
        }
        for name, call in calls.items():
            with pytest.raises(PermissionError):
                await call()
    assert await history_versions(doc.id) == [1]


async def test_actor_outside_the_workspace_is_refused(document_world):
    tenant = document_world.tenants[0]
    with scoped(document_world):
        doc = await create("first private body")

    # A user that is not an active member of the passed workspace is refused
    # before any document is read.
    for user in (document_world.users[2], document_world.users[3]):
        result = await cli("history", doc.id, "--tenant", tenant.slug, "--actor", user.id)
        assert result.exit_code == documents_cli.EXIT_REFUSED, result.stderr
        assert "actor_not_member" in result.stderr
        assert "first private body" not in result.stdout + result.stderr


async def test_foreign_workspace_document_is_unavailable(document_world):
    """Cross-tenant: a document of workspace 0 addressed through workspace 1."""
    with scoped(document_world):
        doc = await create("first private body")
    revision_id = doc.current_revision_id
    tenant_1 = document_world.tenants[1]
    actor_1 = str(document_world.users[2].id)

    with scoped(document_world, workspace=1):
        calls = {
            "history": lambda: documents_cli.history_run(doc.id, tenant_1.slug, actor_1),
            "diff": lambda: documents_cli.diff_run(doc.id, revision_id, revision_id, tenant_1.slug, actor_1),
            "preview": lambda: documents_cli.restore_preview(doc.id, revision_id, tenant_1.slug, actor_1, None),
            "restore": lambda: documents_cli.restore_run(doc.id, revision_id, tenant_1.slug, actor_1, None),
        }
        for name, call in calls.items():
            with pytest.raises(documents_cli.DocumentCommandError) as denied:
                await call()
            assert denied.value.code == "document_unavailable", name
            assert denied.value.exit_code == documents_cli.EXIT_NOT_FOUND

    result = await cli("history", doc.id, "--tenant", tenant_1.slug, "--actor", actor_1)
    assert result.exit_code == documents_cli.EXIT_NOT_FOUND, result.stderr
    assert "document_unavailable" in result.stderr
    assert "first private body" not in result.stdout + result.stderr
    assert await history_versions(doc.id) == [1]


async def test_missing_document_exits_not_found(document_world):
    """A document outside the workspace is unavailable, exactly as the API answers 404."""
    result = await cli(
        "history", 999999, "--tenant", document_world.tenants[0].slug, "--actor", document_world.users[0].id
    )
    assert result.exit_code == documents_cli.EXIT_NOT_FOUND, result.stderr
    assert "document_unavailable" in result.stderr
