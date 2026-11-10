"""Operator reply surface (P15-T08): verified reply origin and an explicit draft.

`reply-preview` only reads. `reply-draft` creates one outbox draft bound to the
source headers the server verified; neither command schedules or sends, and both
run under the same permission scope as the API endpoint.
"""

import asyncio
import json

import typer
from rich.console import Console

from app.core.tenant_context import tenant_scope

app = typer.Typer(name="communications", help="Thread replies: preview the verified origin and create a draft")
console = Console()


def _platform(body):
    """Run one CLI body inside the CLI tenant scope; the body returns a coroutine."""
    with tenant_scope(bypass=True):
        return asyncio.run(body())


@app.command("reply-preview")
def reply_preview(
    thread_id: int = typer.Argument(..., min=1),
    inbound_message_id: int = typer.Argument(..., min=1),
):
    """Show the verified source of a reply: contact, connection and sender identity."""
    # Resolve through the module so a patched handler is visible at call time.
    from app.agent.toolset import communications as tool

    def body():
        return tool.reply_preview(thread_id, inbound_message_id)

    result = _platform(body)
    console.print_json(json.dumps(result, ensure_ascii=False))


@app.command("reply-draft")
def reply_draft(
    thread_id: int = typer.Argument(..., min=1),
    inbound_message_id: int = typer.Argument(..., min=1),
    body: str = typer.Option(..., "--body", "-b", help="Reply body; the subject and headers come from the thread."),
    subject: str = typer.Option("", "--subject", "-s"),
    timezone: str = typer.Option("UTC", "--timezone", "-t"),
    idempotency_key: str = typer.Option(..., "--idempotency-key", help="Stable key; a repeat returns the same draft."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the draft that would be created and change nothing."),
):
    """Create one email reply draft; approval and scheduling stay in the outbox surface."""
    from app.core.database import async_session_maker
    from app.services.communications.reply import create_reply_draft

    if dry_run:
        console.print_json(
            json.dumps(
                {
                    "dry_run": True,
                    "thread_id": thread_id,
                    "inbound_message_id": inbound_message_id,
                    "subject": subject,
                    "timezone": timezone,
                    "writes": False,
                },
                ensure_ascii=False,
            )
        )
        return

    async def _run():
        async with async_session_maker() as session:
            result = await create_reply_draft(
                session,
                thread_id=thread_id,
                inbound_message_id=inbound_message_id,
                subject=subject,
                body=body,
                timezone=timezone,
                idempotency_key=idempotency_key,
            )
            await session.commit()
            return result

    console.print_json(json.dumps(_platform(_run), ensure_ascii=False, default=str))
