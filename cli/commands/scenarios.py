"""Manage bot scenarios via CLI.

Seeding a default scenario per tenant, listing scenarios, etc.
"""

import typer

from app.types import BotTriggerType

app = typer.Typer(name="scenarios", help="Manage bot scenarios")


BASIC_MONITORING_SCOPE = {
    "event_based": True,
    "max_events_per_analysis": 50,
    "sentiment": {
        "categories": ["Позитивный", "Нейтральный", "Негативный"],
        "detect_sarcasm": True,
    },
    "keywords": {
        "entity_types": ["Персоны", "Организации", "Продукты"],
        "max_keywords": 10,
    },
}


def _run(coro):
    """Run an async command as the platform owner (bypass tenant scope)."""
    import asyncio

    from app.core.tenant_context import tenant_scope

    with tenant_scope(bypass=True):
        return asyncio.run(coro)


async def _resolve_tenant_id(value: str) -> int:
    from app.models import Tenant

    if value.isdigit():
        tenant = await Tenant.objects.get(id=int(value))
    else:
        tenant = await Tenant.objects.filter(slug=value).first()
    if tenant is None:
        raise ValueError(f"Workspace {value!r} not found")
    return tenant.id


@app.command("seed-default")
def seed_default(
    tenant: str = typer.Option("owner", "--tenant", help="Workspace slug or id"),
    force: bool = typer.Option(False, "--force", help="Replace existing default scenario"),
):
    """Seed a basic monitoring default scenario for a workspace."""
    from rich import print as rprint

    from app.models import AgentScenario

    async def _seed():
        from app.services.ai.llm_client import resolve_model

        tid = await _resolve_tenant_id(tenant)

        # The CLI runs under `tenant_scope(bypass=True)`, so the manager is not
        # narrowed to one workspace: filter explicitly on the target's id.
        existing = await AgentScenario.objects.filter(
            tenant_id=tid, is_default=True, is_active=True
        )
        existing_sc = existing[0] if existing else None

        if existing_sc and not force:
            rprint(f"[yellow]Default scenario already exists: {existing_sc.name} (id={existing_sc.id})[/yellow]")
            rprint("[dim]Use --force to replace it[/dim]")
            return

        if existing_sc:
            await AgentScenario.objects.update_by_id(existing_sc.id, is_default=False)
            rprint(f"[dim]Cleared is_default on existing scenario {existing_sc.id}[/dim]")

        default_model = await resolve_model()
        text_llm_model_id = default_model.id if default_model else None

        new_scenario = await AgentScenario.objects.create(
            tenant_id=tid,
            name="Базовый мониторинг",
            description="Стандартный сценарий: анализ настроений и ключевых слов.",
            content_types=["text"],
            analysis_types=["sentiment", "keywords"],
            scope=BASIC_MONITORING_SCOPE,
            analyze_type="themes",
            trigger_type=BotTriggerType.KEYWORD_MATCH,
            trigger_config={},
            action_type=None,
            is_active=True,
            is_default=True,
            text_llm_model_id=text_llm_model_id,
        )
        rprint(f"[green]Seeded default scenario: id={new_scenario.id}, name={new_scenario.name!r}[/green]")

    try:
        _run(_seed())
    except Exception as e:
        rprint(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command("list")
def list_scenarios(
    tenant: str = typer.Option("owner", "--tenant", help="Workspace slug or id"),
):
    """List agent scenarios for a workspace."""
    from rich import print as rprint
    from rich.table import Table

    async def _list():
        tid = await _resolve_tenant_id(tenant)
        scenarios = await AgentScenario.objects.filter(tenant_id=tid)

        table = Table(title=f"Agent scenarios — workspace {tid}")
        for col in ("id", "name", "is_default", "is_active", "analysis_types", "content_types"):
            table.add_column(col)
        for s in scenarios:
            table.add_row(
                str(s.id),
                s.name,
                "yes" if s.is_default else "no",
                "yes" if s.is_active else "no",
                ",".join(s.analysis_types or []),
                ",".join(s.content_types or []),
            )
        rprint(table)

    _run(_list())
