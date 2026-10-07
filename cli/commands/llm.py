"""Manage LLM providers and models.

These commands mirror the HTTP API under /api/v1/llm-models and the agent
toolset, giving operators a fast operator surface for bootstrap and debugging.
"""

from __future__ import annotations

import json

import typer
from rich import print as rprint
from rich.table import Table

app = typer.Typer(name="llm", help="Manage LLM providers and models")


def _run(coro):
    import asyncio

    from app.core.tenant_context import tenant_scope

    with tenant_scope(bypass=True):
        return asyncio.run(coro)


@app.command("provider-list")
def provider_list():
    """List all LLM providers."""

    async def _list():
        from app.models import LLMProvider, LLMModel

        providers = await LLMProvider.objects.order_by(LLMProvider.id)
        all_models = await LLMModel.objects.filter(
            provider_id__in=[p.id for p in providers]
        ).all()
        counts: dict[int, int] = {}
        for m in all_models:
            counts[m.provider_id] = counts.get(m.provider_id, 0) + 1

        table = Table(title="LLM Providers")
        for col in (
            "id",
            "name",
            "api_format",
            "base_url",
            "active",
            "default",
            "models",
        ):
            table.add_column(col)
        for p in providers:
            table.add_row(
                str(p.id),
                p.name,
                p.api_format,
                p.base_url,
                "yes" if p.is_active else "no",
                "yes" if p.is_default else "no",
                str(counts.get(p.id, 0)),
            )
        return table

    from rich.console import Console

    Console().print(_run(_list()))


@app.command("model-list")
def model_list(
    provider: int = typer.Option(None, "--provider", help="Filter by provider id"),
    active: bool = typer.Option(None, "--active", help="Filter by active flag"),
    model_type: str = typer.Option(None, "--type", help="Filter by model_type"),
):
    """List all LLM models."""

    async def _list():
        from app.models import LLMModel

        filters: dict = {}
        if provider is not None:
            filters["provider_id"] = provider
        if active is not None:
            filters["is_active"] = active
        if model_type is not None:
            filters["model_type"] = model_type
        rows = await LLMModel.objects.select_related("provider").filter(**filters).order_by(
            LLMModel.id
        )
        table = Table(title="LLM Models")
        for col in (
            "id",
            "provider",
            "name",
            "model_id",
            "type",
            "active",
            "default",
            "max_tokens",
            "temp",
            "cost_in",
            "cost_out",
            "uses",
            "fails",
        ):
            table.add_column(col)
        for m in rows:
            table.add_row(
                str(m.id),
                m.provider.name if m.provider else "?",
                m.name,
                m.model_id,
                m.model_type,
                "yes" if m.is_active else "no",
                "yes" if m.is_default else "no",
                str(m.max_tokens),
                str(m.default_temperature),
                f"${m.input_cost_per_1k:.4f}",
                f"${m.output_cost_per_1k:.4f}",
                str(m.use_count),
                str(m.fail_count),
            )
        return table

    from rich.console import Console

    Console().print(_run(_list()))


@app.command("model-add")
def model_add(
    name: str = typer.Argument(..., help="Human-readable model name"),
    model_id: str = typer.Argument(..., help="Provider model id, e.g. gpt-4o"),
    provider_id: int = typer.Option(..., "--provider-id", "-p", help="LLM provider id"),
    model_type: str = typer.Option("text", "--type", help="text | image | embedding"),
    description: str = typer.Option("", "--description", help="Short description"),
    max_tokens: int = typer.Option(4096, "--max-tokens"),
    default_temperature: float = typer.Option(0.3, "--temperature"),
    input_cost_per_1k: float = typer.Option(0.0, "--cost-in"),
    output_cost_per_1k: float = typer.Option(0.0, "--cost-out"),
    is_active: bool = typer.Option(True, "--active/--inactive"),
    is_default: bool = typer.Option(False, "--default"),
):
    """Create an LLM model."""

    async def _add():
        from app.models import LLMModel

        payload = {
            "name": name,
            "model_id": model_id,
            "provider_id": provider_id,
            "model_type": model_type,
            "description": description or None,
            "max_tokens": max_tokens,
            "default_temperature": default_temperature,
            "input_cost_per_1k": input_cost_per_1k,
            "output_cost_per_1k": output_cost_per_1k,
            "is_active": is_active,
            "is_default": is_default,
        }
        model = await LLMModel.objects.create_model(**payload)
        return model

    try:
        model = _run(_add())
        rprint(f"[green]Created LLM model #{model.id}: {model.name}[/green]")
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)


@app.command("model-update")
def model_update(
    model_id: int = typer.Argument(..., help="LLM model id"),
    name: str = typer.Option(None, "--name"),
    model_id_value: str = typer.Option(None, "--model-id"),
    description: str = typer.Option(None, "--description"),
    model_type: str = typer.Option(None, "--type"),
    max_tokens: int = typer.Option(None, "--max-tokens"),
    default_temperature: float = typer.Option(None, "--temperature"),
    input_cost_per_1k: float = typer.Option(None, "--cost-in"),
    output_cost_per_1k: float = typer.Option(None, "--cost-out"),
    is_active: bool = typer.Option(None, "--active/--inactive"),
    is_default: bool = typer.Option(None, "--default/--no-default"),
):
    """Update an LLM model. Only provided fields are changed."""

    async def _update():
        from app.models import LLMModel

        updates = {
            k: v
            for k, v in {
                "name": name,
                "model_id": model_id_value,
                "description": description,
                "model_type": model_type,
                "max_tokens": max_tokens,
                "default_temperature": default_temperature,
                "input_cost_per_1k": input_cost_per_1k,
                "output_cost_per_1k": output_cost_per_1k,
                "is_active": is_active,
                "is_default": is_default,
            }.items()
            if v is not None
        }
        if not updates:
            rprint("[yellow]No fields to update[/yellow]")
            raise typer.Exit(0)
        return await LLMModel.objects.update_model(model_id, **updates)

    try:
        model = _run(_update())
        if model is None:
            rprint(f"[red]LLM model {model_id} not found[/red]")
            raise typer.Exit(1)
        rprint(f"[green]Updated LLM model #{model.id}: {model.name}[/green]")
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)


@app.command("model-delete")
def model_delete(
    model_id: int = typer.Argument(..., help="LLM model id"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation"),
):
    """Delete an LLM model and reassign the default if needed."""

    if not force:
        confirm = typer.confirm(f"Delete LLM model {model_id}?")
        if not confirm:
            raise typer.Exit(0)

    async def _delete():
        from app.models import LLMModel

        return await LLMModel.objects.delete_with_default_reassignment(model_id)

    try:
        result = _run(_delete())
        rprint(f"[green]Deleted LLM model {model_id}: {result}[/green]")
    except Exception as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)


@app.command("model-test")
def model_test(
    model_id: int = typer.Argument(..., help="LLM model id"),
    prompt: str = typer.Option(
        "Say hello in one short sentence.",
        "--prompt",
        help="Prompt to send when testing the model",
    ),
    mock: bool = typer.Option(False, "--mock", help="Use mock response (no network)"),
):
    """Test an LLM model and print latency / cost / error details."""

    async def _test():
        from app.admin.actions import LLMModelActions
        from app.models import LLMModel

        model = await LLMModel.objects.select_related("provider").get(id=model_id)
        if model is None:
            rprint(f"[red]LLM model {model_id} not found[/red]")
            raise typer.Exit(1)
        if model.provider is None:
            rprint(f"[red]Model {model_id} has no provider[/red]")
            raise typer.Exit(1)

        from app.services.ai.llm_client import LLMClient

        client = LLMClient()
        if mock:
            return {
                "model": model.name,
                "provider": model.provider.name,
                "ok": True,
                "latency_ms": 0,
                "tokens": 3,
                "cost_usd": 0.0,
                "content": "[mock] hello",
            }
        return await LLMModelActions.test_model_real(
            client=client,
            model=model,
            prompt=prompt,
            media_urls=[],
        )

    try:
        result = _run(_test())
        if not isinstance(result, dict):
            rprint(f"[green]{result}[/green]")
            return
        mark = "[green]ok[/green]" if result.get("ok") else "[red]error[/red]"
        rprint(f"{mark} {result.get('model')} via {result.get('provider')}")
        for k in ("latency_ms", "tokens", "cost_usd", "content", "error"):
            if k in result and result[k] is not None:
                rprint(f"  {k}: {result[k]}")
    except Exception as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
