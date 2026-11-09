"""Version history, diff and restore of business documents (P19-T08).

Operator surface for the accepted `app.services.knowledge.versions` service:
the web page and `/api/v1` already expose the same history, diff and restore,
and a CLI operator/bulk/recovery path must not re-implement them.

Access is the endpoint's own check, not a second policy: every read and write
runs inside the target workspace with the resolved operator identity, so
`VersionService` performs the same `source.view`/`source.update` membership
check, ACL scope filter and data-boundary check as `/api/v1/documents`. A
caller that may not read the document in that workspace is refused with
`document_unavailable`; one without the workspace right gets
`document_denied`. Nothing here opens a raw session — the service manages its
own transaction through `with_db_session`.
"""

from __future__ import annotations

import typer

app = typer.Typer(name="documents", help="Document version history, diff and restore")

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_NOT_FOUND = 3
EXIT_CONFLICT = 4
EXIT_INVALID = 5

# The web decision surface owns the approval of a restored revision.
APPROVAL_SURFACE = "/app/documents/manage/{document_id}?boundary=local_only"


class DocumentCommandError(Exception):
    """A CLI-visible refusal with a stable code; carries no document content."""

    def __init__(self, code: str, message: str, exit_code: int = EXIT_REFUSED):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


def _translate(error: Exception) -> DocumentCommandError:
    """Same refusal and error mapping the endpoint uses, as CLI exit codes."""
    from app.services.knowledge.documents import DocumentConflict, DocumentUnavailable

    if isinstance(error, PermissionError):
        return DocumentCommandError("permission_denied", "Permission denied", EXIT_REFUSED)
    if isinstance(error, DocumentUnavailable):
        return DocumentCommandError("document_unavailable", "Document or revision is unavailable", EXIT_NOT_FOUND)
    if isinstance(error, DocumentConflict):
        return DocumentCommandError("document_conflict", "Document is stale or the request conflicts", EXIT_CONFLICT)
    if isinstance(error, ValueError):
        return DocumentCommandError("invalid_request", "Invalid request", EXIT_INVALID)
    return DocumentCommandError("command_failed", "Command failed", EXIT_REFUSED)


async def _run_as(tenant_value: str | None, actor_value: str, coro_fn, *args, **kwargs):
    """Resolve workspace and actor, then run the service under both.

    The outer bypass scope only resolves the workspace slug and the actor's
    active membership and grants; the call itself runs in an interactive scope
    pinned to that workspace with those grants, so no ambient operator grant or
    bypass survives into the permission check.
    """
    from app.core.permissions import operator_permission_scope, permission_scope
    from app.core.tenant_context import tenant_scope
    from app.models import User
    from app.models.managers.tenant_manager import TenantUserManager
    from cli._tenant import resolve_tenant_id

    with tenant_scope(bypass=True), operator_permission_scope():
        tenant_id = await resolve_tenant_id(tenant_value)
        if tenant_id is None:
            raise DocumentCommandError("tenant_required", "Pass --tenant <slug|id>", EXIT_INVALID)
        raw = str(actor_value or "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            raise DocumentCommandError(
                "actor_required", "Pass --actor <user id> of an active workspace member", EXIT_INVALID
            )
        actor_id = int(raw)
        memberships = await TenantUserManager().web_memberships(actor_id)
        membership = next((row for row in memberships if row.tenant_id == tenant_id), None)
        grants = await TenantUserManager().member_grants(membership)
        user = await User.objects.prefetch_related("role.permissions").get(id=actor_id) if membership else None
        if user is None or not user.is_active:
            raise DocumentCommandError(
                "actor_not_member", f"User {actor_id} is not an active member of workspace {tenant_id}", EXIT_REFUSED
            )

    with tenant_scope(tenant_id), permission_scope(user, member_grants=grants):
        try:
            return await coro_fn(*args, **kwargs)
        except DocumentCommandError:
            raise
        except Exception as error:
            raise _translate(error) from None


async def _current(document_id: int):
    from app.services.knowledge.documents import document_service

    return await document_service.get(document_id, boundary="local_only")


async def _revision_body(document_id: int, revision_id: int) -> str:
    """Stored body of one revision; `/diff` is the service's only body reader."""
    from app.services.knowledge.versions import version_service

    return (await version_service.diff(document_id, revision_id, revision_id, boundary="local_only")).parts[0].before


async def _fetch_revision(document_id: int, revision_id: int):
    from app.services.knowledge.versions import version_service

    page = await version_service.list(document_id, boundary="local_only", limit=100)
    for revision in page["items"]:
        if revision.id == revision_id:
            return revision
    raise DocumentCommandError("revision_unavailable", f"Revision {revision_id} is unavailable", EXIT_NOT_FOUND)


async def history_run(document_id: int, tenant: str | None, actor: str, limit: int = 50) -> dict:
    """Revision list plus the current document identity, newest first."""
    from app.services.knowledge.versions import version_service

    async def _read():
        page = await version_service.list(document_id, boundary="local_only", limit=limit)
        current = await _current(document_id)
        return {"document_id": document_id, "current": current, **page}

    return await _run_as(tenant, actor, _read)


async def diff_run(
    document_id: int, from_id: int, to_id: int, tenant: str | None, actor: str, granularity: str = "line"
):
    """Plain-text diff between two revisions of the same document."""
    from app.services.knowledge.versions import version_service

    return await _run_as(
        tenant,
        actor,
        version_service.diff,
        document_id,
        from_id,
        to_id,
        granularity=granularity,
        boundary="local_only",
    )


async def restore_preview(document_id: int, revision_id: int, tenant: str | None, actor: str, description: str | None):
    """What a restore would create; approves nothing and writes nothing."""
    from app.services.knowledge.versions import RestoreRequest

    async def _preview():
        current = await _current(document_id)
        source = await _fetch_revision(document_id, revision_id)
        # Validated here exactly as the write path validates it, so a rejected
        # version fails before an operator trusts the preview.
        RestoreRequest.model_validate(
            {
                "expected_revision_id": current.current_revision_id,
                "expected_updated_at": current.updated_at.isoformat(),
                "change_description": description,
            }
        )
        return {
            "document_id": document_id,
            "current": current,
            "source": source,
            "would_create": {
                "version": current.version + 1,
                "content_hash": source.content_hash,
                "body_text": await _revision_body(document_id, source.id),
                "change_description": description or f"Restored revision {source.version}",
                "status": "draft_invalidated",
            },
            "approval_surface": APPROVAL_SURFACE.format(document_id=document_id),
        }

    return await _run_as(tenant, actor, _preview)


async def restore_run(document_id: int, revision_id: int, tenant: str | None, actor: str, description: str | None):
    """Append a new revision from an older one under expected-version guards."""
    from app.services.knowledge.versions import version_service

    async def _restore():
        # Read the current revision immediately before the write, exactly as
        # the web form posts it: the service then rejects a racing edit.
        current = await _current(document_id)
        data = {
            "expected_revision_id": current.current_revision_id,
            "expected_updated_at": current.updated_at.isoformat(),
            "change_description": description,
        }
        restored = await version_service.restore(document_id, revision_id, data, boundary="local_only")
        return {"restored": restored, "approval_surface": APPROVAL_SURFACE.format(document_id=document_id)}

    return await _run_as(tenant, actor, _restore)


def _print_history(result: dict) -> None:
    from rich import print as rprint
    from rich.table import Table

    table = Table(title=f"Document {result['document_id']} — versions")
    for column in ("id", "version", "hash", "author", "created", "change"):
        table.add_column(column)
    for revision in result["items"]:
        table.add_row(
            str(revision.id),
            str(revision.version),
            revision.content_hash,
            revision.author or "—",
            revision.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            revision.change_description or "—",
        )
    rprint(table)
    if result["next_before_id"] is not None:
        rprint(f"More history before id {result['next_before_id']}")
    approval = APPROVAL_SURFACE.format(document_id=result["document_id"])
    rprint(
        f"current revision {result['current'].current_revision_id}, "
        f"status {result['current'].status}, approval surface {approval}"
    )


def _print_diff(result) -> None:
    from rich import print as rprint

    rprint(f"Diff {result.from_revision_id} -> {result.to_revision_id} ({result.granularity})")
    for part in result.parts:
        if part.operation == "equal":
            rprint(part.before, end="")
            continue
        if part.before:
            rprint(f"- {part.before}", end="")
        if part.after:
            rprint(f"+ {part.after}", end="")
    rprint()


def _print_preview(result: dict) -> None:
    from rich import print as rprint
    from rich.table import Table

    current, source, created = result["current"], result["source"], result["would_create"]
    table = Table(title=f"Document {result['document_id']} — restore preview (nothing written)")
    for column in ("field", "value"):
        table.add_column(column)
    for key, value in (
        ("current version", current.version),
        ("source revision", source.id),
        ("source version", source.version),
        ("new version", created["version"]),
        ("new hash", created["content_hash"]),
        ("new status", created["status"]),
        ("change description", created["change_description"]),
    ):
        table.add_row(key, str(value))
    rprint(table)
    rprint("Body that would become the current revision:")
    rprint(created["body_text"])
    rprint(f"Approval stays in the web decision surface: {result['approval_surface']}")


def _print_restored(result: dict) -> None:
    from rich import print as rprint

    restored = result["restored"]
    rprint(
        f"Restored document {restored.id}: new revision {restored.current_revision_id} "
        f"(version {restored.version}), status {restored.status}"
    )
    rprint(f"Approval stays in the web decision surface: {result['approval_surface']}")


def _emit(message: str, exit_code: int) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(exit_code)


def _invoke(coro_fn, printer=None, **kwargs) -> None:
    """Drive one async command to completion; a refusal exits with its code.

    The whole run happens inside one event loop, so the service's engine is
    used the same way a web request uses it. A CLI command never starts inside
    an already running loop (nothing in `cli/` does); tests call the `*_run`
    functions directly instead.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("document commands cannot run inside an active event loop")

    async def _drive():
        result = await coro_fn(**kwargs)
        if printer is not None:
            printer(result)
        return result

    try:
        asyncio.run(_drive())
    except DocumentCommandError as error:
        typer.echo(f"{error.code}: {error.message}", err=True)
        raise typer.Exit(error.exit_code) from None
    except Exception as error:  # noqa: BLE001 - the CLI reports, never crashes
        translated = _translate(error)
        typer.echo(f"{translated.code}: {translated.message}", err=True)
        raise typer.Exit(translated.exit_code) from None


@app.command("history")
def history(
    document_id: int = typer.Argument(..., help="Document id"),
    tenant: str = typer.Option(..., "--tenant", help="Workspace slug or id"),
    actor: str = typer.Option(..., "--actor", help="Web user id acting in that workspace"),
    limit: int = typer.Option(50, "--limit", min=1, max=100, help="Revisions per page"),
):
    """List document revisions (id, version, hash, author, created, change)."""
    _invoke(history_run, _print_history, document_id=document_id, tenant=tenant, actor=actor, limit=limit)


@app.command("diff")
def diff(
    document_id: int = typer.Argument(..., help="Document id"),
    from_version: int = typer.Argument(..., help="Revision id to compare from"),
    to_version: int = typer.Argument(..., help="Revision id to compare to"),
    tenant: str = typer.Option(..., "--tenant", help="Workspace slug or id"),
    actor: str = typer.Option(..., "--actor", help="Web user id acting in that workspace"),
    granularity: str = typer.Option("line", "--granularity", help="line | word"),
):
    """Show the plain-text diff between two revisions."""
    if granularity not in ("line", "word"):
        _emit("invalid_request: --granularity must be line or word", EXIT_INVALID)
    _invoke(
        diff_run,
        _print_diff,
        document_id=document_id,
        from_id=from_version,
        to_id=to_version,
        tenant=tenant,
        actor=actor,
        granularity=granularity,
    )


@app.command("restore")
def restore(
    document_id: int = typer.Argument(..., help="Document id"),
    revision_id: int = typer.Argument(..., help="Revision id to restore"),
    tenant: str = typer.Option(..., "--tenant", help="Workspace slug or id"),
    actor: str = typer.Option(..., "--actor", help="Web user id acting in that workspace"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the revision that would be created only"),
    description: str = typer.Option(None, "--description", help="Change description for the new revision"),
):
    """Restore a revision as a new current revision; history is never rewritten."""
    if dry_run:
        _invoke(
            restore_preview,
            _print_preview,
            document_id=document_id,
            revision_id=revision_id,
            tenant=tenant,
            actor=actor,
            description=description,
        )
        return
    _invoke(
        restore_run,
        _print_restored,
        document_id=document_id,
        revision_id=revision_id,
        tenant=tenant,
        actor=actor,
        description=description,
    )
