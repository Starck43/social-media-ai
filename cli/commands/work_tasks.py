"""Canonical work-task CLI (P22-T07).

`worktasks` is the operator transport for the canonical task; it calls the same
agent-toolset handlers as the assistant, which call the same services as
`/api/v2/tasks`. There is no CLI-only rule: rights, versions, admission and
confirmation all come from the service behind the command.
"""

from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console

from app.core.tenant_context import tenant_scope

app = typer.Typer(
    name="worktasks",
    help="Canonical work tasks: list, create, edit, plan, run, close, reopen, schedule, decide",
)
console = Console()

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


_SCOPE: dict[str, str | None] = {"tenant": None, "user": None}


@app.callback()
def _worktasks_scope(
    tenant: str = typer.Option(..., "--tenant", help="Workspace slug or id"),
    user: int = typer.Option(..., "--user", min=1, help="Active workspace member; the canonical actor"),
) -> None:
    """Canonical task commands run as one explicit workspace member, never as a bypass."""
    _SCOPE["tenant"], _SCOPE["user"] = tenant, str(user)


def _run(body):
    """One CLI body as the declared actor inside the declared workspace."""
    from sqlalchemy import select

    from app.core.access_catalog import workspace_grants
    from app.core.database import async_session_maker
    from app.core.permissions import permission_scope
    from app.models import TenantUser, User, WorkspaceRole
    from cli._tenant import resolve_tenant_id

    async def _main():
        tenant_id = await resolve_tenant_id(_SCOPE["tenant"])
        if tenant_id is None:
            raise ValueError("work_task_tenant_required")
        actor = await User.objects.get(id=int(_SCOPE["user"]), is_active=True)
        if actor is None:
            raise PermissionError("work_task_actor_required")
        # Rights come from this member's own role, never from the CLI's operator
        # grant: the command must behave exactly like that member's session.
        async with async_session_maker() as session:
            role = await session.scalar(
                select(WorkspaceRole.codename)
                .join(TenantUser, TenantUser.role_id == WorkspaceRole.id)
                .where(
                    TenantUser.tenant_id == tenant_id,
                    TenantUser.user_id == actor.id,
                    TenantUser.is_active.is_(True),
                )
            )
        if role is None:
            raise PermissionError("work_task_actor_required")
        with tenant_scope(tenant_id), permission_scope(actor, member_grants=workspace_grants(role)):
            return await body()

    try:
        result = asyncio.run(_main())
    except PermissionError:
        console.print_json(json.dumps({"error": "Permission denied", "code": "work_task_denied"}, ensure_ascii=False))
        raise typer.Exit(EXIT_FAILED) from None
    console.print_json(json.dumps(result, ensure_ascii=False, default=str))
    if isinstance(result, dict) and result.get("error"):
        raise typer.Exit(EXIT_FAILED)
    return result


def _request_key(value: str | None) -> str:
    import uuid

    key = (value or "").strip() or uuid.uuid4().hex
    if not 1 <= len(key) <= 128:
        console.print_json(json.dumps({"error": "Request key must be 1-128 characters", "code": "invalid_arguments"}))
        raise typer.Exit(EXIT_USAGE)
    return key


@app.command("list")
def list_cmd(
    limit: int = typer.Option(50, "--limit", min=1, max=100),
    before_id: int | None = typer.Option(None, "--before-id", min=1),
    view: str | None = typer.Option(None, "--view", help="mine|team"),
):
    """List canonical tasks visible to the caller."""
    from app.agent.toolset import work_tasks as tool

    _run(lambda: tool.work_task_list(limit=limit, before_id=before_id, view=view))


@app.command("show")
def show_cmd(task_id: int = typer.Argument(..., min=1)):
    """Read one task by canonical id."""
    from app.agent.toolset import work_tasks as tool

    _run(lambda: tool.work_task_get(task_id))


@app.command("create")
def create_cmd(
    title: str = typer.Option(..., "--title", "-t", help="Task title"),
    description: str = typer.Option("", "--description", "-d"),
    visibility: str = typer.Option("private", "--visibility", help="private|shared|workspace"),
    priority: str = typer.Option("medium", "--priority"),
    due_date: str | None = typer.Option(None, "--due-date"),
    due_at: str | None = typer.Option(None, "--due-at", help="ISO 8601 with offset"),
    timezone: str = typer.Option("UTC", "--timezone"),
    recurrence: str | None = typer.Option(None, "--recurrence"),
):
    """Create one canonical task."""
    from app.agent.toolset import work_tasks as tool

    payload = {
        "title": title,
        "description": description,
        "visibility": visibility,
        "priority": priority,
        "timezone": timezone,
    }
    for key, value in (("due_date", due_date), ("due_at", due_at), ("recurrence", recurrence)):
        if value is not None:
            payload[key] = value
    _run(lambda: tool.work_task_create(payload))


@app.command("edit")
def edit_cmd(
    task_id: int = typer.Argument(..., min=1),
    expected_version: int = typer.Option(..., "--version", min=1),
    scope: str = typer.Option("occurrence", "--scope", help="occurrence|series"),
    title: str | None = typer.Option(None, "--title"),
    description: str | None = typer.Option(None, "--description"),
    priority: str | None = typer.Option(None, "--priority"),
    status: str | None = typer.Option(None, "--status"),
):
    """Edit a task; a stale version is refused instead of overwritten."""
    from app.agent.toolset import work_tasks as tool

    changes = {"expected_version": expected_version, "scope": scope}
    for key, value in (("title", title), ("description", description), ("priority", priority), ("status", status)):
        if value is not None:
            changes[key] = value
    _run(lambda: tool.work_task_edit(task_id, changes))


@app.command("preview")
def preview_cmd(
    task_id: int = typer.Argument(..., min=1),
    plan_file: str = typer.Option(..., "--plan", help="Path to a JSON execution plan"),
):
    """Validate a plan without publishing or running it."""
    from pathlib import Path

    from app.agent.toolset import work_tasks as tool

    try:
        plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        console.print_json(json.dumps({"error": "Plan must be a readable JSON object", "code": "invalid_arguments"}))
        raise typer.Exit(EXIT_USAGE) from None
    _run(lambda: tool.work_task_preview(task_id, plan))


@app.command("complete")
def complete_cmd(
    task_id: int = typer.Argument(..., min=1),
    expected_version: int = typer.Option(..., "--version", min=1),
):
    """Complete work on a task with its expected version."""
    from app.agent.toolset import work_tasks as tool

    _run(lambda: tool.work_task_complete(task_id, expected_version))


@app.command("close")
def close_cmd(
    task_id: int = typer.Argument(..., min=1),
    confirm: str | None = typer.Option(None, "--confirm", help="Token from the preview"),
):
    """Preview the effects of closing the task version, or confirm with its token."""
    from app.agent.toolset import work_tasks as tool

    if confirm is None:
        _run(lambda: tool.work_task_close_preview(task_id))
        return
    _run(lambda: tool.work_task_close(task_id, confirm))


@app.command("reopen")
def reopen_cmd(
    task_id: int = typer.Argument(..., min=1),
    reactivate_schedule: bool = typer.Option(
        False, "--reactivate-schedule", help="Re-arm a paused schedule as part of the reopening"
    ),
    confirm: str | None = typer.Option(None, "--confirm", help="Token from the preview"),
):
    """Preview reopening this closed task, or confirm with its token."""
    from app.agent.toolset import work_tasks as tool

    if confirm is None:
        _run(lambda: tool.work_task_reopen_preview(task_id, reactivate_schedule))
        return
    _run(lambda: tool.work_task_reopen(task_id, confirm, reactivate_schedule))


@app.command("schedule")
def schedule_cmd(
    task_id: int = typer.Argument(..., min=1),
    revision: int = typer.Option(..., "--revision", min=1, help="Published plan revision"),
    enabled: bool = typer.Option(..., "--on/--off", help="Arm or pause the schedule"),
    expected_version: int = typer.Option(..., "--version", min=1),
):
    """Pause or arm the published schedule; a manual run stays a separate command."""
    from app.agent.toolset import work_tasks as tool

    _run(lambda: tool.work_task_schedule(task_id, enabled, revision, expected_version))


@app.command("delete")
def delete_cmd(
    task_id: int = typer.Argument(..., min=1),
    scope: str = typer.Option("occurrence", "--scope", help="occurrence|series"),
    confirm: str | None = typer.Option(None, "--confirm", help="Signed token from the preview"),
):
    """Preview a deletion, or confirm it with the token the preview returned."""
    from app.agent.toolset import work_tasks as tool

    if confirm is None:
        _run(lambda: tool.work_task_delete_preview(task_id, scope))
        return
    _run(lambda: tool.work_task_delete(task_id, scope, confirm))


@app.command("run")
def run_cmd(
    task_id: int = typer.Argument(..., min=1),
    request_key: str | None = typer.Option(None, "--request-key", help="Stable key; a repeat returns the same receipt"),
):
    """Run the current published revision manually."""
    from app.agent.toolset import work_tasks as tool

    key = _request_key(request_key)
    _run(lambda: tool.work_task_run(task_id, key))


@app.command("runs")
def runs_cmd(
    task_id: int = typer.Argument(..., min=1),
    limit: int = typer.Option(50, "--limit", min=1, max=100),
    before_id: int | None = typer.Option(None, "--before-id", min=1),
):
    """Show run receipts, newest first."""
    from app.agent.toolset import work_tasks as tool

    _run(lambda: tool.work_task_runs(task_id, limit=limit, before_id=before_id))


@app.command("actions")
def actions_cmd(task_id: int = typer.Argument(..., min=1)):
    """Show available actions and pending decisions."""
    from app.agent.toolset import work_tasks as tool

    _run(lambda: tool.work_task_actions(task_id))


@app.command("decide")
def decide_cmd(
    task_id: int = typer.Argument(..., min=1),
    reference: str = typer.Option(..., "--reference", help="local:<id>, command:<id>, document:<id>"),
    outcome: str = typer.Option("accepted", "--outcome", help="accepted|rejected"),
    preview: bool = typer.Option(False, "--preview", help="Only prepare; prints the signed confirmation token"),
    confirm: str | None = typer.Option(None, "--confirm", help="Token from the preview"),
    reason: str = typer.Option("", "--reason"),
):
    """Preview a decision, then close it with the returned token."""
    from app.agent.toolset import work_tasks as tool

    if confirm is None:
        if not preview:
            console.print_json(
                json.dumps({"error": "Pass --preview to prepare or --confirm to decide", "code": "invalid_arguments"})
            )
            raise typer.Exit(EXIT_USAGE)
        _run(lambda: tool.work_task_decision_preview(task_id, reference, outcome))
        return
    _run(lambda: tool.work_task_decide(task_id, confirm, reason))


@app.command("resolve")
def resolve_cmd(
    namespace: str = typer.Argument(..., min=1),
    legacy_id: int = typer.Argument(..., min=1),
):
    """Resolve an opaque legacy reference to its canonical task."""
    from app.agent.toolset import work_tasks as tool

    _run(lambda: tool.work_task_resolve_legacy(namespace, legacy_id))
