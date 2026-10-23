"""Manage LLM providers and models.

These commands mirror the HTTP API under /api/v1/llm/llm-models and the agent
toolset, giving operators a fast operator surface for bootstrap and debugging.
"""

from __future__ import annotations

import json

import typer
from rich import print as rprint
from rich.table import Table

app = typer.Typer(name="llm", help="Manage LLM providers and models")

_CATALOG_ACTOR_REQUIRED = (
    "Personal LLM catalog access requires an authenticated active user. "
    "Open the authenticated personal model settings to view or test your own models."
)


def _run(coro):
    import asyncio

    from app.core.tenant_context import tenant_scope

    with tenant_scope(bypass=True):
        return asyncio.run(coro)


def _catalog_owner_id() -> int:
    """Trusted personal catalog actor from the existing permission context.

    Tenant bypass alone is not an owner and no ID flag, environment value,
    workspace owner, admin role or first catalog row may stand in for one.
    """
    from app.core.personal_catalog_context import current_catalog_owner_id
    from app.services.ai.personal_catalog import require_actor

    try:
        owner_id = current_catalog_owner_id()
        # A worker-bound catalog scope without the matching permission actor is
        # not CLI authentication either.
        require_actor(owner_id)
    except PermissionError:
        rprint(f"[red]{_CATALOG_ACTOR_REQUIRED}[/red]")
        raise typer.Exit(1) from None
    if type(owner_id) is not int or owner_id <= 0:
        rprint(f"[red]{_CATALOG_ACTOR_REQUIRED}[/red]")
        raise typer.Exit(1)
    return owner_id


@app.command("provider-list")
def provider_list():
    """List LLM providers in the authenticated personal catalog."""
    owner_id = _catalog_owner_id()

    async def _list():
        from app.services.ai.personal_catalog import catalog_metadata

        catalog = await catalog_metadata(user_id=owner_id)
        table = Table(title="LLM Providers")
        for col in (
            "id",
            "name",
            "api_format",
            "base_url",
            "active",
            "models",
        ):
            table.add_column(col)
        for p in catalog["providers"]:
            table.add_row(
                str(p["id"]),
                p["name"],
                p["api_format"],
                p["base_url"],
                "yes" if p["is_active"] else "no",
                str(len(p["models"])),
            )
        return table

    from rich.console import Console

    try:
        table = _run(_list())
    except Exception:
        rprint("[red]Personal LLM catalog could not be read. Check the database connection and configuration.[/red]")
        raise typer.Exit(1) from None
    Console().print(table)


@app.command("model-list")
def model_list(
    provider: int = typer.Option(None, "--provider", help="Filter by provider id"),
    active: bool = typer.Option(None, "--active", help="Filter by active flag"),
    model_type: str = typer.Option(None, "--type", help="Filter by model_type"),
):
    """List LLM models in the authenticated personal catalog."""
    owner_id = _catalog_owner_id()

    async def _list():
        from app.models import LLMModel

        filters: dict = {"owner_user_id": owner_id}
        if provider is not None:
            filters["provider_id"] = provider
        if active is not None:
            filters["is_active"] = active
        if model_type is not None:
            filters["model_type"] = model_type
        rows = await LLMModel.objects.select_related("provider").filter(**filters).order_by(LLMModel.id)
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

    try:
        table = _run(_list())
    except Exception:
        rprint("[red]Personal LLM catalog could not be read. Check the database connection and configuration.[/red]")
        raise typer.Exit(1) from None
    Console().print(table)


@app.command("model-add")
def model_add(
    name: str = typer.Argument(..., help="Human-readable model name"),
    model_id: str = typer.Argument(..., help="Provider model id, e.g. gpt-4o"),
    provider_id: int = typer.Option(..., "--provider-id", "-p", help="LLM provider id"),
    model_type: str = typer.Option("text", "--type", help="text | image | embedding | decision"),
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
    model_id: int = typer.Argument(..., help="LLM model id (positive PostgreSQL INTEGER)"),
    prompt: str = typer.Option(
        "Say hello in one short sentence.",
        "--prompt",
        help="Local mock prompt (1–8000 characters); never sent by this command",
    ),
    mock: bool = typer.Option(False, "--mock", help="Use shared local mock (DB read, no provider request)"),
):
    """Run a local mock; paid probes require the authenticated admin surface."""
    if type(model_id) is not int or not 1 <= model_id <= 2_147_483_647:
        rprint("[red]Model id must be a positive PostgreSQL INTEGER.[/red]")
        raise typer.Exit(1)
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000:
        rprint("[red]Prompt must contain 1–8000 characters and not be blank.[/red]")
        raise typer.Exit(1)
    if not mock:
        # Tenant bypass is not an authenticated actor/payer. Never restore the
        # old implicit paid dispatch, even when personal billing is disabled.
        rprint("[red]Real CLI probes are unavailable without an authenticated payer.[/red]")
        rprint("Use --mock for a local check, or the authenticated admin model test for a real request.")
        raise typer.Exit(1)

    owner_id = _catalog_owner_id()

    async def _test():
        from app.core.personal_catalog_context import assert_catalog_model
        from app.models import LLMModel
        from app.services.ai.model_probe import probe_mock

        model = await LLMModel.objects.select_related("provider").get(id=model_id, owner_user_id=owner_id)
        if model is None or model.provider is None:
            raise ValueError("Model or provider unavailable")
        assert_catalog_model(model)
        # The shared mock intentionally does not check/decrypt provider keys or
        # require an active provider: it is not a readiness/reachability verdict.
        result = probe_mock(model, prompt)
        if not isinstance(result, dict) or result.get("ok", True) is not True:
            raise ValueError("Mock probe failed")
        if not isinstance(result.get("content"), str) or not result["content"].strip():
            raise ValueError("Mock probe returned no content")
        return {
            "mode": "mock",
            "ok": True,
            "is_real_response": False,
            "tokens_spent": 0,
            "content": result["content"],
            "simulated_usage": result.get("usage"),
            "estimated_cost_usd": result.get("cost"),
        }

    try:
        # JSON escapes control characters; disabling Rich markup prevents model
        # labels from becoming terminal styles/links. Never print raw exceptions
        # or extra diagnostics (keys, prompt echoes, provider bodies).
        report = json.dumps(_run(_test()), ensure_ascii=False)
    except Exception:
        rprint("[red]Local model test failed. Check the model/provider configuration.[/red]")
        raise typer.Exit(1) from None

    from rich.console import Console

    Console().print(report, markup=False, highlight=False, soft_wrap=True)
