
import typer

# Configure logging (console + file -> logs/app.log) for CLI runs.
import app.core.logger  # noqa: F401  (configures the "app" logger handlers)

from .commands import chains, collect, collection, credentials, direct, llm, roles, scenarios, tasks
from .commands.tasks import (
    _add_task,
    _check_task_sources,
    _remove_task,
    _run_platform,
    _run_task,
    _set_task_sources,
    _split_ints,
    _split_names,
    _split_phrases,
    task_app,
)

app = typer.Typer(
    name="AI Assistant CLI",
    help="AI Assistant CLI",
    no_args_is_help=True,
)

app.add_typer(roles.app, name="roles", help="Manage roles and permissions")
app.add_typer(credentials.app, name="credentials", help="Manage platform credentials (tenant vault)")
app.add_typer(llm.app, name="llm", help="Manage LLM providers and models")
app.add_typer(chains.app, name="chains", help="Manage topic chains")
app.add_typer(scenarios.app, name="scenarios", help="Manage agent scenarios")
app.add_typer(collection.app, name="collection", help="Manage task-scoped collection lanes")
app.command(name="collect")(collect.collect_cmd)

# Direct job-type commands at top level: `cli.main analyze|prune|learn|reflect`.
app.command(name="analyze")(direct.cmd_analyze)
app.command(name="prune")(direct.cmd_prune)
app.command(name="learn")(direct.cmd_learn)
app.command(name="reflect")(direct.cmd_reflect)

app.add_typer(task_app, name="task")

digest_app = typer.Typer(help="Digest operations")


@digest_app.command("send-now")
def digest_send_now(
    period: str = typer.Argument("day", help="Period: day | week"),
    group_by: str = typer.Option(
        "themes", "--group-by", help="Grouping axis: themes | sources | entities | intent | topic_chains"
    ),
    time_breakdown: bool = typer.Option(
        False, "--time-breakdown", help="Enable per-date sub-entries within each group"
    ),
):
    """Build and publish a digest right now (manual run, not idempotent)."""

    import rich
    from rich import print as rprint

    if period not in ("day", "week"):
        rprint("[red]period must be 'day' or 'week'[/red]")
        raise typer.Exit(1)

    from app.services.digest.builder import DigestDeliveryError, build_and_publish

    async def _run():
        try:
            result = await build_and_publish(period=period, group_by=group_by, time_breakdown=time_breakdown)
        except DigestDeliveryError as e:
            rprint(f"[red]Delivery failed: {e}[/red]")
            raise typer.Exit(1)
        if result.get("text"):
            rich.print(result["text"])
        rprint(f"\n[bold]Status:[/bold] {result.get('status')}")
        if result.get("results"):
            for channel, res in result["results"].items():
                mark = "[green]ok[/green]" if res.get("success") else f"[red]{res.get('error')}[/red]"
                rprint(f"  {channel}: {mark}")

    _run_platform(_run())


app.add_typer(digest_app, name="digest")


@digest_app.command("run")
def digest_run(
    src: str = typer.Option(None, "--src", "-s", help="Source ids, urls or platform (vk/telegram/max)"),
    tenant: str = typer.Option(None, "--tenant", help="Workspace slug or id (empty = all active)"),
    period: str = typer.Option("day", "--period", help="Period: day | week"),
    group_by: str = typer.Option(
        "themes", "--group-by", help="Grouping axis: themes | sources | entities | intent | topic_chains"
    ),
    time_breakdown: bool = typer.Option(
        False, "--time-breakdown", help="Enable per-date sub-entries within each group"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
):
    """Build and publish a digest directly (not idempotent)."""

    _run_platform(
        direct._digest_main(
            period=period, src=src, tenant=tenant, group_by=group_by, time_breakdown=time_breakdown, verbose=verbose
        )
    )


# app.add_typer(permissions.app, name="permissions", help="Manage permissions")


@app.callback()
def callback():
    """Social Media Manager Administration CLI"""


if __name__ == "__main__":
    app()
