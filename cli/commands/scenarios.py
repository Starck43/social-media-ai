"""Manage bot scenarios via CLI.

Seeding a default scenario per tenant, listing scenarios, etc.
"""

import typer

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

    from app.services.ai.scenario_builder import ScenarioBuilder

    async def _list():
        tid = await _resolve_tenant_id(tenant)
        scenarios = await AgentScenario.objects.filter(tenant_id=tid)

        table = Table(title=f"Agent scenarios — workspace {tid}")
        for col in ("id", "name", "is_default", "is_active", "analysis_types", "media_types"):
            table.add_column(col)
        for s in scenarios:
            table.add_row(
                str(s.id),
                s.name,
                "yes" if s.is_default else "no",
                "yes" if s.is_active else "no",
                ",".join(s.analysis_types or []),
                ",".join(ScenarioBuilder.media_types_for(s.content_types or [])),
            )
        rprint(table)

    _run(_list())


@app.command("output-schema")
def output_schema(
    scenario_id: int = typer.Argument(..., help="Scenario id"),
    add_field: list[str] = typer.Option([], "--add-field", help="Extra output field as 'name:type' (repeatable)"),
    require_field: list[str] = typer.Option([], "--require", help="Field name to mark as required (repeatable)"),
    base_prompt_file: str = typer.Option(None, "--base-prompt-file", help="UTF-8 file with the new base prompt"),
    tenant: str = typer.Option("owner", "--tenant", help="Workspace slug or id"),
    apply: bool = typer.Option(False, "--apply", help="Write changes (default: dry-run)"),
):
    """Set an explicit output_schema for a scenario (dry-run unless --apply).

    The stored schema is extended with the requested fields; the derived
    contract of the scenario stays intact. Without --apply nothing is written.
    """
    import difflib
    import json
    from pathlib import Path

    from rich import print as rprint

    from app.services.ai.scenario_output_fields import (
        base_output_schema,
        merge_extra_fields,
        parse_field_spec,
    )

    def _diff(stored: dict, merged: dict) -> str:
        before = json.dumps(stored or {}, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
        after = json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
        lines = list(difflib.unified_diff(before, after, "stored", "new", lineterm=""))
        return "\n".join(lines) or "(no change)"

    try:
        # The field specs and the prompt file are checked before any database
        # access, so a typo never reaches the scenario row.
        extra_fields = {}
        for spec in add_field or []:
            name, prop = parse_field_spec(spec)
            extra_fields[name] = prop

        new_prompt = None
        if base_prompt_file:
            path = Path(base_prompt_file)
            if not path.is_file():
                raise ValueError(f"Prompt file not found: {path}")
            new_prompt = path.read_text(encoding="utf-8").rstrip("\n")
            if not new_prompt.strip():
                raise ValueError(f"Prompt file is empty: {path}")
    except Exception as e:
        rprint(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    async def _run_output_schema():
        from app.models import AgentScenario

        tid = await _resolve_tenant_id(tenant)
        scenario = await AgentScenario.objects.filter(tenant_id=tid, id=scenario_id).first()
        if scenario is None:
            raise ValueError(f"Scenario {scenario_id} not found in workspace {tid}")

        stored = scenario.output_schema or {}
        # A stored schema wins over the derived one: re-deriving would drop
        # fields that were added earlier.
        base, origin = base_output_schema(stored, scenario.analysis_types or [], scenario.scope or {})
        merged, warnings = merge_extra_fields(base, extra_fields, list(require_field or []))

        for warning in warnings:
            rprint(f"[yellow]Warning: {warning}[/yellow]")

        rprint(f"[dim]Scenario {scenario_id} (workspace {tid}), base schema: {origin}[/dim]")
        rprint("[bold]output_schema that would be stored:[/bold]")
        rprint(json.dumps(merged, ensure_ascii=False, indent=2))
        rprint("[bold]Diff against the stored schema:[/bold]")
        rprint(_diff(stored, merged))

        if new_prompt is not None:
            rprint("[bold]base_prompt — current:[/bold]")
            rprint(scenario.base_prompt or "<empty>")
            rprint("[bold]base_prompt — new:[/bold]")
            rprint(new_prompt)

        if not apply:
            rprint("[yellow]Dry-run: nothing was written (pass --apply to store)[/yellow]")
            return

        updates = {"output_schema": merged}
        if new_prompt is not None:
            updates["base_prompt"] = new_prompt
        # Per-row ORM write: the persistence guard validates the contract on flush.
        await AgentScenario.objects.update_by_id(scenario_id, **updates)
        saved = await AgentScenario.objects.get(id=scenario_id)
        rprint("[green]Stored output_schema:[/green]")
        rprint(json.dumps(saved.output_schema, ensure_ascii=False, indent=2))
        if new_prompt is not None:
            rprint("[green]Stored base_prompt:[/green]")
            rprint(saved.base_prompt or "<empty>")

    try:
        _run(_run_output_schema())
    except Exception as e:
        rprint(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
